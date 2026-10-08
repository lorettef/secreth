"""Register Telegram identity and open invite or club menu."""

import logging
from uuid import UUID

from aiogram import Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import Message

from app.bot.keyboards.membership import club_menu, club_picker, join_invite
from app.bot.keyboards.sessions import recovery
from app.locales import ru
from app.services.membership import MembershipError, MembershipService, TelegramProfile
from app.services.sessions import SessionError, SessionErrorCode, SessionService

router = Router()
logger = logging.getLogger(__name__)


@router.message(CommandStart())
async def start(
    message: Message, command: CommandObject, membership_service: MembershipService,
    session_service: SessionService,
) -> None:
    if message.from_user is None:
        return
    user = message.from_user
    try:
        await membership_service.register_telegram_user(
            TelegramProfile(user.id, user.username, user.first_name, user.last_name)
        )
        if command.args and command.args.startswith("recovery_"):
            try:
                request_id = UUID(command.args[len("recovery_"):])
            except ValueError:
                raise SessionError(SessionErrorCode.STALE) from None
            view = await session_service.recovery_view(user.id, request_id)
            await message.answer(ru.recovery_message(view), reply_markup=recovery(view))
            return
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
                reply_markup=club_menu(club, await session_service.menu_state(user.id, club.id)),
            )
        else:
            await message.answer(ru.CHOOSE_CLUB, reply_markup=club_picker(clubs))
    except MembershipError as error:
        await message.answer(ru.ERRORS[error.code.value])
    except SessionError as error:
        await message.answer(ru.SESSION_ERRORS[error.code.value])
    except Exception:
        logger.exception("Start command failed")
        await message.answer(ru.GENERIC_ERROR)
