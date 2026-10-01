import asyncio
import logging
import time
from datetime import timedelta

from telegram import Bot, Update
from telegram.constants import ChatMemberStatus, ChatType
from telegram.error import (
    BadRequest,
    Conflict,
    Forbidden,
    InvalidToken,
    RetryAfter,
    TelegramError,
)

from topic_police_bot.config import Settings
from topic_police_bot.moderation import Moderator

logger = logging.getLogger(__name__)

POLL_TIMEOUT = 25


class StartupError(Exception):
    pass


async def validate_chat(bot: Bot, settings: Settings) -> None:
    try:
        chat = await bot.get_chat(settings.chat_id)
    except BadRequest as error:
        if str(error).casefold() != "chat not found":
            raise
        raise StartupError(
            "Chat not found: check CHAT_ID is the full Bot API ID of the target supergroup "
            "and the bot for BOT_TOKEN is a member of that group. "
            "Process environment variables override .env. See README: Chat not found"
        ) from error
    if chat.type != ChatType.SUPERGROUP or not chat.is_forum:
        raise StartupError("CHAT_ID must identify a supergroup with topics enabled")
    if chat.has_protected_content:
        raise StartupError("Disable content protection in the chat to allow forwarding")
    member = await bot.get_chat_member(settings.chat_id, bot.id)
    if (
        member.status != ChatMemberStatus.ADMINISTRATOR
        or not member.can_delete_messages
    ):
        raise StartupError(
            "The bot must be a chat administrator with Delete messages permission"
        )


async def poll(bot: Bot, moderator: Moderator) -> None:
    offset: int | None = None
    retry_delay = 1.0
    while True:
        try:
            updates = await bot.get_updates(
                offset=offset,
                limit=100,
                timeout=POLL_TIMEOUT,
                read_timeout=5,
                allowed_updates=[Update.MESSAGE, Update.MY_CHAT_MEMBER],
            )
        except Conflict, Forbidden, InvalidToken:
            moderator.observation_lost()
            raise
        except TelegramError as error:
            moderator.observation_lost()
            delay = retry_delay
            if isinstance(error, RetryAfter):
                delay = error.retry_after
                if isinstance(delay, timedelta):
                    delay = delay.total_seconds()
            logger.warning(
                "Polling failed (%s); retry in %s seconds", type(error).__name__, delay
            )
            await asyncio.sleep(delay)
            retry_delay = min(retry_delay * 2, 30.0)
            continue

        now = time.monotonic()
        wall_time = time.time()
        moderator.observation_resumed(now, wall_time)
        retry_delay = 1.0
        for update in sorted(updates, key=lambda item: item.update_id):
            if offset is not None and update.update_id < offset:
                continue
            moderator.receive(update, now, wall_time)
            offset = update.update_id + 1


async def run(settings: Settings) -> None:
    async with Bot(settings.bot_token.get_secret_value()) as bot:
        await validate_chat(bot, settings)
        await bot.delete_webhook(drop_pending_updates=True)
        moderator = Moderator(settings, bot)
        logger.info("Long polling started")
        try:
            await poll(bot, moderator)
        finally:
            await moderator.close()
