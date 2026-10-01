import re
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent.parent / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
        populate_by_name=True,
    )

    bot_token: SecretStr = Field(validation_alias="BOT_TOKEN")
    chat_id: int = Field(validation_alias="CHAT_ID", lt=0)
    watched_user_id: int = Field(validation_alias="WATCHED_USER_ID", gt=0)
    window_minutes: int = Field(default=10, validation_alias="WINDOW_MINUTES", gt=0)

    @field_validator("chat_id")
    @classmethod
    def validate_chat_id(cls, chat_id: int) -> int:
        if chat_id >= -1_000_000_000_000:
            raise ValueError(
                "CHAT_ID must be a full Bot API supergroup ID, for example -1001234567890; "
                "use message.chat.id, not a topic ID or a number with only a minus added. "
                "See README: Chat not found"
            )
        return chat_id

    @field_validator("bot_token")
    @classmethod
    def validate_bot_token(cls, token: SecretStr) -> SecretStr:
        if re.fullmatch(r"[1-9][0-9]*:[A-Za-z0-9_-]+", token.get_secret_value()) is None:
            raise ValueError("BOT_TOKEN must have the Telegram bot token format: numeric ID:secret")
        return token

    @property
    def window_seconds(self) -> int:
        return self.window_minutes * 60
