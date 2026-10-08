"""Admin-only XLSX download; workbook stays in memory and leaves no disk files."""

from aiogram import Router
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from app.bot.callbacks import ExportClub
from app.bot.handlers.common import reply
from app.locales import ru
from app.services.games import GameError
from app.services.spreadsheets import ExportService

router = Router()


@router.callback_query(ExportClub.filter())
async def export_club(
    callback: CallbackQuery, callback_data: ExportClub, export_service: ExportService
):
    try:
        filename, payload = await export_service.export(
            callback.from_user.id, callback_data.club_id
        )
    except GameError as error:
        await reply(callback, ru.GAME_ERRORS[error.code.value])
        return
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer_document(
            BufferedInputFile(payload, filename=filename),
            caption=ru.EXPORT_READY,
            parse_mode=None,
        )
