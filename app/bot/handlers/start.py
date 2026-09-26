"""Register Telegram identity and open invite or club menu."""

import logging

from aiogram import Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import Message

from app.bot.keyboards.membership import club_menu, club_picker, join_invite
from app.locales import ru
from app.services.membership import MembershipError, MembershipService, TelegramProfile

router = Router()
logger = logging.getLogger(__name__)


@router.message(CommandStart())
async def start(
    message: Message, command: CommandObject, membership_service: MembershipService
) -> None:
    if message.from_user is None:
        return
    user = message.from_user
    try:
        await membership_service.register_telegram_user(
            TelegramProfile(user.id, user.username, user.first_name, user.last_name)
        )
        if command.args and command.args.startswith("invite_"):
            invitation = await membership_service.preview_invite(command.args[len("invite_") :])
            await message.answer(
                ru.invite_message(invitation.club_name),
                reply_markup=join_invite(invitation.id),
            )
            return
        clubs = await membership_service.list_clubs(user.id)
        if not clubs:
            await message.answer(ru.NO_MEMBERSHIP)
        elif len(clubs) == 1:
            club = clubs[0]
            await message.answer(
                ru.club_menu_message(club.name, club.player_name, club.has_pending_link),
                reply_markup=club_menu(club),
            )
        else:
            await message.answer(ru.CHOOSE_CLUB, reply_markup=club_picker(clubs))
    except MembershipError as error:
        await message.answer(ru.ERRORS[error.code.value])
    except Exception:
        logger.exception("Start command failed")
        await message.answer(ru.GENERIC_ERROR)
