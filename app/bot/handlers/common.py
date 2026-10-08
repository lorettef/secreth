"""Safe replies for Telegram callbacks."""

import logging

from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from app.locales import ru
from app.services.membership import MembershipError

logger = logging.getLogger(__name__)


async def reply(
    callback: CallbackQuery, text: str, keyboard: InlineKeyboardMarkup | None = None
) -> None:
    if isinstance(callback.message, Message):
        await callback.answer()
        await callback.message.answer(text, reply_markup=keyboard, parse_mode=None)
    else:
        await callback.answer(text, show_alert=True)


async def reply_error(callback: CallbackQuery, error: MembershipError) -> None:
    await reply(callback, ru.ERRORS[error.code.value])


async def reply_unexpected(callback: CallbackQuery) -> None:
    logger.exception("Onboarding callback failed")
    await reply(callback, ru.GENERIC_ERROR)
