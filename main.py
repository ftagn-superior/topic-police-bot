import asyncio
import logging

from pydantic import ValidationError
from telegram.error import Conflict, TelegramError

from topic_police_bot.config import Settings
from topic_police_bot.runtime import StartupError, run


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logger = logging.getLogger(__name__)
    try:
        settings = Settings()
    except ValidationError as error:
        for issue in error.errors(include_input=False, include_context=False):
            logger.error("Configuration %s: %s", ".".join(map(str, issue["loc"])), issue["msg"])
        return 1
    try:
        asyncio.run(run(settings))
    except KeyboardInterrupt:
        logger.info("Bot stopped")
    except StartupError as error:
        logger.error("Startup check: %s", error)
        return 1
    except Conflict:
        logger.error("Polling conflict: stop other instances of this bot and remove its webhook")
        return 1
    except TelegramError as error:
        logger.error("Telegram request failed (%s); check token, connection and bot permissions", type(error).__name__)
        return 1
    except Exception as error:
        logger.error("Bot stopped after an unexpected error (%s)", type(error).__name__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
