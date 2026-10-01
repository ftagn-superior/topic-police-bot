import os
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from topic_police_bot.config import Settings


class SettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = {
            "BOT_TOKEN": "123456:unit_test_token",
            "CHAT_ID": "-1001234567890",
            "WATCHED_USER_ID": "123456789",
            "WINDOW_MINUTES": "10",
        }
        environment_patch = patch.dict(os.environ, self.environment, clear=True)
        environment_patch.start()
        self.addCleanup(environment_patch.stop)

    def test_settings_from_environment(self) -> None:
        settings = Settings(_env_file=None)

        self.assertEqual(settings.bot_token.get_secret_value(), self.environment["BOT_TOKEN"])
        self.assertEqual(settings.chat_id, -1001234567890)
        self.assertEqual(settings.watched_user_id, 123456789)
        self.assertEqual(settings.window_seconds, 600)
        self.assertNotIn(self.environment["BOT_TOKEN"], repr(settings))

    def test_default_window(self) -> None:
        del os.environ["WINDOW_MINUTES"]

        self.assertEqual(Settings(_env_file=None).window_minutes, 10)

    def test_missing_required_settings(self) -> None:
        for name in ("BOT_TOKEN", "CHAT_ID", "WATCHED_USER_ID"):
            with self.subTest(name=name), patch.dict(os.environ, self.environment, clear=True):
                del os.environ[name]
                with self.assertRaises(ValidationError):
                    Settings(_env_file=None)

    def test_invalid_settings(self) -> None:
        for name, value in (
            ("BOT_TOKEN", ""),
            ("BOT_TOKEN", "not-a-token"),
            ("CHAT_ID", "1234567890"),
            ("CHAT_ID", "-1234567890"),
            ("WATCHED_USER_ID", "0"),
            ("WINDOW_MINUTES", "0"),
            ("WINDOW_MINUTES", "not-a-number"),
        ):
            with self.subTest(name=name, value=value), patch.dict(os.environ, {name: value}):
                with self.assertRaises(ValidationError):
                    Settings(_env_file=None)
