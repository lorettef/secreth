"""Buttons for membership and administrator onboarding flows."""

from uuid import UUID

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.callbacks import (
    ChoosePlayer,
    ClubAction,
    JoinInvite,
    PlayerPage,
    ReviewLink,
    ReviewPage,
    SessionAction,
    SessionStart,
    StatsPage,
    HistoryPage,
)
from app.domain.enums import MembershipRole
from app.locales import ru
from app.services.membership import ClubView, PendingLinkView, PlayerChoice
from app.services.sessions import SessionMenuState


def join_invite(invite_id: UUID) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=ru.JOIN_BUTTON, callback_data=JoinInvite(invite_id=invite_id).pack())]
        ]
    )


def club_picker(clubs: tuple[ClubView, ...]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=club.name, callback_data=ClubAction(club_id=club.id, action="menu").pack())]
            for club in clubs
        ]
    )


def club_menu(club: ClubView, session_state: SessionMenuState | None = None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if session_state and session_state.active_session_id:
        rows.append([InlineKeyboardButton(text=ru.OPEN_SESSION_BUTTON, callback_data=SessionAction(
            session_id=session_state.active_session_id, action="view").pack())])
    elif session_state and session_state.can_start:
        rows.append([InlineKeyboardButton(text=ru.START_SESSION_BUTTON, callback_data=SessionStart(
            club_id=club.id).pack())])
    for label, section in ((ru.STATS_MY_BUTTON, "my"), (ru.STATS_BUTTON, "menu"), (ru.RATINGS_BUTTON, "ratings")):
        rows.append([InlineKeyboardButton(text=label, callback_data=StatsPage(club_id=club.id, section=section).pack())])
    rows.append([InlineKeyboardButton(text=ru.HISTORY_BUTTON,
                callback_data=HistoryPage(scope_id=club.id, section="menu").pack())])
    if club.player_name is None and not club.has_pending_link:
        rows.extend(
            [
                [InlineKeyboardButton(text=ru.NEW_PLAYER_BUTTON, callback_data=ClubAction(club_id=club.id, action="new").pack())],
                [InlineKeyboardButton(text=ru.EXISTING_PLAYER_BUTTON, callback_data=PlayerPage(club_id=club.id, page=0).pack())],
            ]
        )
    if club.role is MembershipRole.ADMIN:
        rows.append(
            [InlineKeyboardButton(text=ru.ADMIN_BUTTON, callback_data=ClubAction(club_id=club.id, action="admin").pack())]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def player_choices(
    club_id: UUID, players: tuple[PlayerChoice, ...], page: int
) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=player.display_name, callback_data=ChoosePlayer(player_id=player.id).pack())]
        for player in players
    ]
    pagination: list[InlineKeyboardButton] = []
    if page > 0:
        pagination.append(InlineKeyboardButton(text=ru.PREVIOUS_BUTTON, callback_data=PlayerPage(club_id=club_id, page=page - 1).pack()))
    if len(players) == 10:
        pagination.append(InlineKeyboardButton(text=ru.NEXT_BUTTON, callback_data=PlayerPage(club_id=club_id, page=page + 1).pack()))
    if pagination:
        rows.append(pagination)
    rows.append([InlineKeyboardButton(text=ru.BACK_BUTTON, callback_data=ClubAction(club_id=club_id, action="menu").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_menu(club_id: UUID) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=ru.HISTORY_PLAYERS_ADMIN,
                                  callback_data=HistoryPage(scope_id=club_id, section="manage").pack())],
            [InlineKeyboardButton(text=ru.HISTORY_AUDIT,
                                  callback_data=HistoryPage(scope_id=club_id, section="audit").pack())],
            [InlineKeyboardButton(text=ru.CREATE_INVITE_BUTTON, callback_data=ClubAction(club_id=club_id, action="invite").pack())],
            [InlineKeyboardButton(text=ru.CREATE_INVITE_ONCE_BUTTON, callback_data=ClubAction(club_id=club_id, action="invite1").pack())],
            [InlineKeyboardButton(text=ru.CREATE_INVITE_WEEK_BUTTON, callback_data=ClubAction(club_id=club_id, action="invite7").pack())],
            [InlineKeyboardButton(text=ru.PENDING_LINKS_BUTTON, callback_data=ReviewPage(club_id=club_id, page=0).pack())],
            [InlineKeyboardButton(text=ru.BACK_BUTTON, callback_data=ClubAction(club_id=club_id, action="menu").pack())],
        ]
    )


def back_to_club(club_id: UUID) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=ru.BACK_BUTTON, callback_data=ClubAction(club_id=club_id, action="menu").pack())]
        ]
    )


def pending_links(
    club_id: UUID, requests: tuple[PendingLinkView, ...], page: int
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for request in requests:
        rows.append([InlineKeyboardButton(text=ru.pending_label(request), callback_data=ReviewLink(request_id=request.id, action="show").pack())])
    pagination: list[InlineKeyboardButton] = []
    if page > 0:
        pagination.append(InlineKeyboardButton(text=ru.PREVIOUS_BUTTON, callback_data=ReviewPage(club_id=club_id, page=page - 1).pack()))
    if len(requests) == 10:
        pagination.append(InlineKeyboardButton(text=ru.NEXT_BUTTON, callback_data=ReviewPage(club_id=club_id, page=page + 1).pack()))
    if pagination:
        rows.append(pagination)
    rows.append([InlineKeyboardButton(text=ru.BACK_BUTTON, callback_data=ClubAction(club_id=club_id, action="admin").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def review_link(request_id: UUID, club_id: UUID) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=ru.APPROVE_BUTTON, callback_data=ReviewLink(request_id=request_id, action="approve").pack())],
            [InlineKeyboardButton(text=ru.REJECT_BUTTON, callback_data=ReviewLink(request_id=request_id, action="reject").pack())],
            [InlineKeyboardButton(text=ru.BACK_BUTTON, callback_data=ReviewPage(club_id=club_id, page=0).pack())],
        ]
    )
