"""Button flows for joining a club and choosing a player identity."""

from datetime import datetime, timedelta, timezone

from aiogram import Router
from aiogram.types import CallbackQuery
from aiogram.utils.deep_linking import create_start_link

from app.bot.callbacks import ChoosePlayer, ClubAction, JoinInvite, PlayerPage
from app.bot.handlers.common import reply, reply_error, reply_unexpected
from app.bot.keyboards.membership import admin_menu, back_to_club, club_menu, player_choices
from app.locales import ru
from app.services.membership import MembershipError, MembershipService

router = Router()


@router.callback_query(JoinInvite.filter())
async def join_club(
    callback: CallbackQuery, callback_data: JoinInvite, membership_service: MembershipService
) -> None:
    try:
        club = await membership_service.join_invite(callback.from_user.id, callback_data.invite_id)
        await reply(
            callback,
            ru.club_menu_message(club.name, club.player_name, club.has_pending_link),
            club_menu(club),
        )
    except MembershipError as error:
        await reply_error(callback, error)
    except Exception:
        await reply_unexpected(callback)


@router.callback_query(ClubAction.filter())
async def club_action(
    callback: CallbackQuery, callback_data: ClubAction, membership_service: MembershipService
) -> None:
    try:
        club_id = callback_data.club_id
        if callback_data.action == "new":
            club = await membership_service.create_new_player(callback.from_user.id, club_id)
            await reply(callback, ru.club_menu_message(club.name, club.player_name, False), club_menu(club))
        elif callback_data.action == "menu":
            club = await membership_service.get_club(callback.from_user.id, club_id)
            await reply(
                callback,
                ru.club_menu_message(club.name, club.player_name, club.has_pending_link),
                club_menu(club),
            )
        elif callback_data.action == "admin":
            club = await membership_service.get_admin_club(callback.from_user.id, club_id)
            await reply(callback, ru.admin_menu_message(club.name), admin_menu(club_id))
        elif callback_data.action == "profile":
            club = await membership_service.get_club(callback.from_user.id, club_id)
            if club.player_name is None:
                await reply(callback, ru.PROFILE_UNAVAILABLE)
                return
            await reply(callback, ru.profile_message(club.name, club.player_name), back_to_club(club_id))
        elif callback_data.action in ("invite", "invite1", "invite7"):
            max_uses = 1 if callback_data.action == "invite1" else None
            expires_at = (
                datetime.now(timezone.utc) + timedelta(days=7)
                if callback_data.action == "invite7"
                else None
            )
            token = await membership_service.create_invite(
                callback.from_user.id, club_id, max_uses=max_uses, expires_at=expires_at
            )
            link = await create_start_link(callback.bot, f"invite_{token}")
            await reply(callback, ru.invite_created_message(link), admin_menu(club_id))
        else:
            await reply(callback, ru.INVALID_ACTION)
    except MembershipError as error:
        await reply_error(callback, error)
    except Exception:
        await reply_unexpected(callback)


@router.callback_query(PlayerPage.filter())
async def show_unlinked_players(
    callback: CallbackQuery, callback_data: PlayerPage, membership_service: MembershipService
) -> None:
    try:
        club = await membership_service.get_club(callback.from_user.id, callback_data.club_id)
        players = await membership_service.list_unlinked_players(
            callback.from_user.id, club.id, page=callback_data.page
        )
        text = ru.player_choices_message(club.name) if players else ru.NO_UNLINKED_PLAYERS
        await reply(callback, text, player_choices(club.id, players, callback_data.page))
    except MembershipError as error:
        await reply_error(callback, error)
    except Exception:
        await reply_unexpected(callback)


@router.callback_query(ChoosePlayer.filter())
async def choose_existing_player(
    callback: CallbackQuery, callback_data: ChoosePlayer, membership_service: MembershipService
) -> None:
    try:
        await membership_service.request_player_link(callback.from_user.id, callback_data.player_id)
        await reply(callback, ru.LINK_REQUESTED)
    except MembershipError as error:
        await reply_error(callback, error)
    except Exception:
        await reply_unexpected(callback)
