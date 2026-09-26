"""Initial Telegram command."""

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from app.locales.ru import STARTED

router = Router()


@router.message(CommandStart())
async def start(message: Message) -> None:
    await message.answer(STARTED)
