"""Minimal administrator review of historical player links."""

from aiogram import Router
from aiogram.types import CallbackQuery

from app.bot.callbacks import ReviewLink, ReviewPage
from app.bot.handlers.common import reply, reply_error, reply_unexpected
from app.bot.keyboards.membership import admin_menu, pending_links, review_link
from app.locales import ru
from app.services.membership import MembershipError, MembershipService

router = Router()


@router.callback_query(ReviewPage.filter())
async def list_pending(
    callback: CallbackQuery, callback_data: ReviewPage, membership_service: MembershipService
) -> None:
    try:
        requests = await membership_service.list_pending_links(
            callback.from_user.id, callback_data.club_id, page=callback_data.page
        )
        text = ru.PENDING_LINKS_BUTTON if requests else ru.NO_PENDING_LINKS
        await reply(
            callback,
            text,
            pending_links(callback_data.club_id, requests, callback_data.page),
        )
    except MembershipError as error:
        await reply_error(callback, error)
    except Exception:
        await reply_unexpected(callback)


@router.callback_query(ReviewLink.filter())
async def review_pending(
    callback: CallbackQuery, callback_data: ReviewLink, membership_service: MembershipService
) -> None:
    try:
        club_id, request = await membership_service.get_pending_link(
            callback.from_user.id, callback_data.request_id
        )
        if callback_data.action == "show":
            await reply(callback, ru.pending_details(request), review_link(request.id, club_id))
        elif callback_data.action in ("approve", "reject"):
            approve = callback_data.action == "approve"
            await membership_service.review_player_link(
                callback.from_user.id, request.id, approve=approve
            )
            await reply(
                callback,
                ru.LINK_APPROVED if approve else ru.LINK_REJECTED,
                admin_menu(club_id),
            )
        else:
            await reply(callback, ru.INVALID_ACTION)
    except MembershipError as error:
        await reply_error(callback, error)
    except Exception:
        await reply_unexpected(callback)
