import asyncio
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, call

from telegram import Bot, Chat, Message, MessageId, Update, User
from telegram.error import NetworkError

from topic_police_bot.config import Settings
from topic_police_bot.moderation import Moderator


class ModerationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.settings = Settings(
            _env_file=None,
            BOT_TOKEN="123456:unit_test_token",
            CHAT_ID=-1001234567890,
            WATCHED_USER_ID=123456789,
            WINDOW_MINUTES=1,
        )
        self.bot = Mock(spec=Bot)
        self.bot.forward_messages.return_value = (MessageId(101),)
        self.bot.delete_messages.return_value = True
        self.moderator = Moderator(self.settings, self.bot)
        self.now = time.monotonic()
        self.wall_time = time.time()
        for elapsed in (60, 30, 0):
            self.moderator.observation_resumed(self.now - elapsed, self.wall_time - elapsed)
        self.addAsyncCleanup(self.moderator.close)

    def message(self, author_id: int, topic: int | None = None, **kwargs) -> Message:
        return Message(
            message_id=10,
            date=datetime.fromtimestamp(self.wall_time, timezone.utc),
            chat=Chat(self.settings.chat_id, "supergroup", is_forum=True),
            from_user=User(author_id, "Test", False),
            text="Test message",
            is_topic_message=topic is not None,
            message_thread_id=topic,
            **kwargs,
        )

    async def receive(self, message: Message) -> None:
        self.moderator.receive(Update(1, message=message), self.now, self.wall_time)
        await asyncio.gather(*self.moderator.tasks)

    async def test_forwards_before_deleting_original(self) -> None:
        await self.receive(self.message(42, topic=7))
        await self.receive(self.message(self.settings.watched_user_id))

        self.assertEqual(self.bot.mock_calls, [
            call.forward_messages(
                chat_id=self.settings.chat_id,
                from_chat_id=self.settings.chat_id,
                message_ids=(10,),
                message_thread_id=7,
            ),
            call.delete_messages(chat_id=self.settings.chat_id, message_ids=(10,)),
        ])

    async def test_warmup_prevents_transfer(self) -> None:
        self.moderator.observation_lost()
        self.moderator.observation_resumed(self.now, self.wall_time)
        await self.receive(self.message(42, topic=7))
        await self.receive(self.message(self.settings.watched_user_id))

        self.bot.forward_messages.assert_not_awaited()

    async def test_general_activity_prevents_transfer(self) -> None:
        await self.receive(self.message(42, topic=7))
        await self.receive(self.message(42))
        await self.receive(self.message(self.settings.watched_user_id))

        self.bot.forward_messages.assert_not_awaited()

    async def test_multiple_active_topics_prevent_transfer(self) -> None:
        await self.receive(self.message(42, topic=7))
        await self.receive(self.message(43, topic=8))
        await self.receive(self.message(self.settings.watched_user_id))

        self.bot.forward_messages.assert_not_awaited()

    async def test_replies_and_albums_remain_in_general(self) -> None:
        await self.receive(self.message(42, topic=7))
        for extra in ({"reply_to_message": self.message(42)}, {"media_group_id": "album-1"}):
            with self.subTest(extra=extra):
                await self.receive(self.message(self.settings.watched_user_id, **extra))

        self.bot.forward_messages.assert_not_awaited()
        self.bot.delete_messages.assert_not_awaited()

    async def test_polling_gap_restarts_warmup(self) -> None:
        await self.receive(self.message(42, topic=7))
        self.now += 46
        self.wall_time += 46
        self.moderator.observation_resumed(self.now, self.wall_time)
        await self.receive(self.message(42, topic=7))
        await self.receive(self.message(self.settings.watched_user_id))

        self.bot.forward_messages.assert_not_awaited()

    async def test_expired_activity_does_not_select_topic(self) -> None:
        old_message = Message(
            message_id=9,
            date=datetime.fromtimestamp(self.wall_time - 61, timezone.utc),
            chat=Chat(self.settings.chat_id, "supergroup", is_forum=True),
            from_user=User(42, "Test", False),
            text="Old message",
            is_topic_message=True,
            message_thread_id=7,
        )
        await self.receive(old_message)
        await self.receive(self.message(self.settings.watched_user_id))

        self.bot.forward_messages.assert_not_awaited()

    async def test_failed_forward_retains_original(self) -> None:
        self.bot.forward_messages.side_effect = NetworkError("Test failure")
        await self.receive(self.message(42, topic=7))
        with self.assertLogs("topic_police_bot.moderation", level="ERROR"):
            await self.receive(self.message(self.settings.watched_user_id))

        self.bot.forward_messages.assert_awaited_once()
        self.bot.delete_messages.assert_not_awaited()

    async def test_unconfirmed_forward_retains_original(self) -> None:
        self.bot.forward_messages.return_value = ()
        await self.receive(self.message(42, topic=7))
        with self.assertLogs("topic_police_bot.moderation", level="ERROR"):
            await self.receive(self.message(self.settings.watched_user_id))

        self.bot.delete_messages.assert_not_awaited()
