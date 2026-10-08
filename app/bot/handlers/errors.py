"""Final safety net; domain errors continue to be handled by their own flows."""

import logging
from aiogram.types import ErrorEvent, Message
from app.locales import ru

logger = logging.getLogger(__name__)


async def unexpected_error(event: ErrorEvent) -> bool:
    error = event.exception
    logger.error(
        "Unexpected Telegram error: update=%s",
        event.update.update_id,
        exc_info=(type(error), error, error.__traceback__),
    )
    try:
        if event.update.callback_query:
            await event.update.callback_query.answer(ru.GENERIC_ERROR, show_alert=True)
        elif isinstance(event.update.message, Message):
            await event.update.message.answer(ru.GENERIC_ERROR, parse_mode=None)
    except Exception:
        logger.exception("Could not deliver error response")
    return True
