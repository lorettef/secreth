"""Buttons for current evening and presence changes."""

from uuid import UUID

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.callbacks import ClubAction, RecoveryAction, SessionAction, SessionPick, SessionStep, StatsSession
from app.locales import ru
from app.services.sessions import RecoveryView, RosterPlayer, SessionView


def session_view(view: SessionView) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if view.can_manage:
        for label, action in (
            (ru.RECORD_GAME_BUTTON, "record"),
            (ru.CHANGE_ROSTER_BUTTON, "roster"),
            (ru.TRANSFER_BUTTON, "transfer"),
            (ru.FINISH_BUTTON, "finish"),
        ):
            if action == "record" and len(view.roster) < 5:
                continue
            rows.append([InlineKeyboardButton(text=label, callback_data=SessionAction(
                session_id=view.id, action=action).pack())])
    rows.append([InlineKeyboardButton(text=ru.STATS_SESSION_BUTTON,
                callback_data=StatsSession(session_id=view.id).pack())])
    if view.can_recover:
        rows.append([InlineKeyboardButton(text=ru.SESSION_HELP_BUTTON, callback_data=SessionAction(
            session_id=view.id, action="help").pack())])
    rows.append([InlineKeyboardButton(text=ru.BACK_BUTTON, callback_data=ClubAction(
        club_id=view.club_id, action="menu").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def start_choices(players: tuple[RosterPlayer, ...], selected: set[UUID], page: int, flow: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(
        text=("✅ " if p.id in selected else "▫️ ") + p.name,
        callback_data=SessionPick(flow=flow, player_id=p.id).pack(),
    )] for p in players[page * 10:(page + 1) * 10]]
    navigation = []
    if page:
        navigation.append(InlineKeyboardButton(text=ru.PREVIOUS_BUTTON, callback_data=SessionStep(flow=flow, action="prev").pack()))
    if (page + 1) * 10 < len(players):
        navigation.append(InlineKeyboardButton(text=ru.NEXT_BUTTON, callback_data=SessionStep(flow=flow, action="next").pack()))
    if navigation:
        rows.append(navigation)
    rows.append([InlineKeyboardButton(text=ru.START_SELECTED_BUTTON, callback_data=SessionStep(flow=flow, action="create").pack())])
    rows.append([InlineKeyboardButton(text=ru.CANCEL_BUTTON, callback_data=SessionStep(flow=flow, action="cancel").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def roster_actions(session_id: UUID) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=ru.ARRIVED_BUTTON, callback_data=SessionAction(session_id=session_id, action="add").pack())],
        [InlineKeyboardButton(text=ru.LEFT_BUTTON, callback_data=SessionAction(session_id=session_id, action="remove").pack())],
        [InlineKeyboardButton(text=ru.BACK_BUTTON, callback_data=SessionAction(session_id=session_id, action="view").pack())],
    ])


def player_choices(players: tuple[RosterPlayer, ...], page: int, flow: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=p.name, callback_data=SessionPick(flow=flow, player_id=p.id).pack())]
            for p in players[page * 10:(page + 1) * 10]]
    navigation = []
    if page:
        navigation.append(InlineKeyboardButton(text=ru.PREVIOUS_BUTTON, callback_data=SessionStep(flow=flow, action="prev").pack()))
    if (page + 1) * 10 < len(players):
        navigation.append(InlineKeyboardButton(text=ru.NEXT_BUTTON, callback_data=SessionStep(flow=flow, action="next").pack()))
    if navigation:
        rows.append(navigation)
    rows.append([InlineKeyboardButton(text=ru.CANCEL_BUTTON, callback_data=SessionStep(flow=flow, action="cancel").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm(action: str, flow: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=ru.CONFIRM_BUTTON, callback_data=SessionStep(flow=flow, action=action).pack())],
        [InlineKeyboardButton(text=ru.CANCEL_BUTTON, callback_data=SessionStep(flow=flow, action="cancel").pack())],
    ])


def flow_roster_actions(flow: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=SessionStep(flow=flow, action=action).pack())]
        for label, action in ((ru.ARRIVED_BUTTON, "add"), (ru.LEFT_BUTTON, "remove"), (ru.BACK_BUTTON, "back"))
    ])


def recovery_help(flow: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=ru.RECOVERY_START_BUTTON,
                              callback_data=SessionStep(flow=flow, action="recovery").pack())],
        [InlineKeyboardButton(text=ru.BACK_BUTTON, callback_data=SessionStep(flow=flow, action="back").pack())],
    ])


def recovery(view: RecoveryView) -> InlineKeyboardMarkup:
    rows = []
    if view.can_confirm:
        rows.append([InlineKeyboardButton(text=ru.RECOVERY_CONFIRM_BUTTON,
                    callback_data=RecoveryAction(request_id=view.id, action="confirm").pack())])
    rows.append([InlineKeyboardButton(text=ru.RECOVERY_REFRESH_BUTTON,
                callback_data=RecoveryAction(request_id=view.id, action="view").pack())])
    rows.append([InlineKeyboardButton(text=ru.BACK_BUTTON,
                callback_data=SessionAction(session_id=view.session_id, action="view").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)
