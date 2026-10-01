import asyncio
import logging
import time
from collections.abc import Coroutine

from telegram import Bot, Update
from telegram.constants import ChatMemberStatus, ChatType
from telegram.error import TelegramError
from telegram.ext import filters

from topic_police_bot.config import Settings

logger = logging.getLogger(__name__)

MAX_OBSERVATION_GAP = 45.0


class Moderator:
    def __init__(self, settings: Settings, bot: Bot) -> None:
        self.settings = settings
        self.bot = bot
        self.observed_since: float | None = None
        self.last_poll_at: float | None = None
        self.last_poll_wall_time: float | None = None
        self.generation = 0
        self.can_moderate = True
        self.topic_activity: dict[int, float] = {}
        self.uncertain_topics: dict[int, float] = {}
        self.general_activity: float | None = None
        self.tasks: set[asyncio.Task[None]] = set()
        self.transfer_lock = asyncio.Lock()

    def observation_resumed(self, now: float, wall_time: float | None = None) -> None:
        if wall_time is None:
            wall_time = time.time()
        self._check_continuity(now, wall_time)
        self.last_poll_at = now
        self.last_poll_wall_time = wall_time
        if self.observed_since is None:
            self.observed_since = now
            logger.info("Observation started; warming up for %s minutes", self.settings.window_minutes)
        self._prune(now)

    def observation_lost(self) -> None:
        self.observed_since = None
        self.last_poll_at = None
        self.last_poll_wall_time = None
        self.generation += 1
        self.topic_activity.clear()
        self.uncertain_topics.clear()
        self.general_activity = None
        logger.warning("Observation interrupted; a full warm-up window is required")

    def _check_continuity(self, now: float, wall_time: float) -> None:
        if self.last_poll_at is not None and self.last_poll_wall_time is not None and (
            now - self.last_poll_at > MAX_OBSERVATION_GAP
            or wall_time - self.last_poll_wall_time > MAX_OBSERVATION_GAP
            or wall_time < self.last_poll_wall_time
        ):
            self.observation_lost()

    def _prune(self, now: float) -> None:
        cutoff = now - self.settings.window_seconds
        self.topic_activity = {
            topic: timestamp for topic, timestamp in self.topic_activity.items()
            if timestamp > cutoff
        }
        self.uncertain_topics = {
            topic: timestamp for topic, timestamp in self.uncertain_topics.items()
            if timestamp > cutoff
        }
        if self.general_activity is not None and self.general_activity <= cutoff:
            self.general_activity = None

    def _target(self, now: float) -> int | None:
        self._prune(now)
        if not self.can_moderate or self.observed_since is None:
            return None
        if now - self.observed_since < self.settings.window_seconds:
            return None
        active_topics = self.topic_activity.keys() | self.uncertain_topics.keys()
        if self.general_activity is not None or len(active_topics) != 1 or len(self.topic_activity) != 1:
            return None
        return next(iter(self.topic_activity))

    def receive(self, update: Update, now: float, wall_time: float) -> None:
        membership = update.my_chat_member
        if membership is not None and membership.chat.id == self.settings.chat_id:
            self.observation_lost()
            member = membership.new_chat_member
            self.can_moderate = (
                member.status == ChatMemberStatus.ADMINISTRATOR and member.can_delete_messages
            )
            if not self.can_moderate:
                logger.warning("Moderation disabled until administrator and Delete messages permissions are restored")
            return

        message = update.message
        if message is None or message.chat_id != self.settings.chat_id:
            return
        if message.chat.type != ChatType.SUPERGROUP or not message.chat.is_forum:
            self.observation_lost()
            return
        if filters.StatusUpdate.ALL.check_update(update):
            return

        author = message.from_user
        uncertain_author = message.sender_chat is not None or author is None
        if not uncertain_author and author.is_bot:
            return

        topic = None
        if message.is_topic_message:
            if message.message_thread_id is None or message.message_thread_id <= 1:
                self.observation_lost()
                return
            topic = message.message_thread_id

        age = max(0.0, wall_time - message.date.timestamp())
        activity_time = now - age
        if age >= self.settings.window_seconds:
            return
        watched = not uncertain_author and author.id == self.settings.watched_user_id
        if not watched:
            if topic is None:
                self.general_activity = max(self.general_activity or activity_time, activity_time)
            elif uncertain_author:
                self.uncertain_topics[topic] = max(self.uncertain_topics.get(topic, activity_time), activity_time)
            else:
                self.topic_activity[topic] = max(self.topic_activity.get(topic, activity_time), activity_time)
            return
        if topic is not None or message.media_group_id is not None:
            return

        eligible = not (
            message.reply_to_message
            or message.external_reply
            or message.reply_to_story
            or message.has_protected_content
        )
        target = self._target(now) if eligible else None
        if target is not None:
            self._spawn(self._transfer((message.message_id,), target, self.generation))

    async def _transfer(
        self, message_ids: tuple[int, ...], target: int, generation: int
    ) -> None:
        async with self.transfer_lock:
            self._check_continuity(time.monotonic(), time.time())
            if generation != self.generation:
                return
            try:
                forwarded = await self.bot.forward_messages(
                    chat_id=self.settings.chat_id,
                    from_chat_id=self.settings.chat_id,
                    message_ids=message_ids,
                    message_thread_id=target,
                )
            except TelegramError as error:
                logger.error(
                    "Forward failed or outcome unknown (%s); originals retained: %s; target=%s",
                    type(error).__name__, message_ids, target,
                )
                return

            copied_ids = tuple(message.message_id for message in forwarded)
            if len(copied_ids) != len(message_ids) or len(set(copied_ids)) != len(message_ids):
                logger.error(
                    "Partial forward; originals retained: %s; copies=%s; target=%s",
                    message_ids, copied_ids, target,
                )
                return
            self._check_continuity(time.monotonic(), time.time())
            if generation != self.generation:
                logger.warning(
                    "Observation interrupted during forwarding; originals retained: %s; copies=%s; target=%s",
                    message_ids, copied_ids, target,
                )
                return
            try:
                deleted = await self.bot.delete_messages(
                    chat_id=self.settings.chat_id, message_ids=message_ids,
                )
            except TelegramError as error:
                logger.error(
                    "Delete failed or outcome unknown (%s); no retry: originals=%s; copies=%s; target=%s",
                    type(error).__name__, message_ids, copied_ids, target,
                )
                return
            if not deleted:
                logger.error("Delete not confirmed; originals=%s; copies=%s", message_ids, copied_ids)
                return
            logger.info("Moved messages=%s to topic=%s; copies=%s", message_ids, target, copied_ids)

    def _spawn(self, coroutine: Coroutine[None, None, None]) -> None:
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task[None]) -> None:
        self.tasks.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.error("Processing failed (%s)", type(error).__name__)
            self.observation_lost()

    async def close(self) -> None:
        tasks = tuple(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
