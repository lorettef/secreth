"""Buttons for current evening and presence changes."""

from uuid import UUID

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.callbacks import ClubAction, SessionAction, SessionPick, SessionStep
from app.locales import ru
from app.services.sessions import RosterPlayer, SessionView


def session_view(view: SessionView) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if view.can_manage:
        for label, action in (
            (ru.RECORD_GAME_BUTTON, "record"),
            (ru.CHANGE_ROSTER_BUTTON, "roster"),
            (ru.SESSION_SUMMARY_BUTTON, "summary"),
            (ru.TRANSFER_BUTTON, "transfer"),
            (ru.FINISH_BUTTON, "finish"),
        ):
            rows.append([InlineKeyboardButton(text=label, callback_data=SessionAction(
                session_id=view.id, action=action).pack())])
    rows.append([InlineKeyboardButton(text=ru.BACK_BUTTON, callback_data=ClubAction(
        club_id=view.club_id, action="menu").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def start_choices(players: tuple[RosterPlayer, ...], selected: set[UUID], page: int) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(
        text=("✅ " if p.id in selected else "▫️ ") + p.name,
        callback_data=SessionPick(player_id=p.id).pack(),
    )] for p in players[page * 10:(page + 1) * 10]]
    navigation = []
    if page:
        navigation.append(InlineKeyboardButton(text=ru.PREVIOUS_BUTTON, callback_data=SessionStep(action="prev").pack()))
    if (page + 1) * 10 < len(players):
        navigation.append(InlineKeyboardButton(text=ru.NEXT_BUTTON, callback_data=SessionStep(action="next").pack()))
    if navigation:
        rows.append(navigation)
    rows.append([InlineKeyboardButton(text=ru.START_SELECTED_BUTTON, callback_data=SessionStep(action="create").pack())])
    rows.append([InlineKeyboardButton(text=ru.CANCEL_BUTTON, callback_data=SessionStep(action="cancel").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def roster_actions(session_id: UUID) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=ru.ARRIVED_BUTTON, callback_data=SessionAction(session_id=session_id, action="add").pack())],
        [InlineKeyboardButton(text=ru.LEFT_BUTTON, callback_data=SessionAction(session_id=session_id, action="remove").pack())],
        [InlineKeyboardButton(text=ru.BACK_BUTTON, callback_data=SessionAction(session_id=session_id, action="view").pack())],
    ])


def player_choices(players: tuple[RosterPlayer, ...], page: int) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=p.name, callback_data=SessionPick(player_id=p.id).pack())]
            for p in players[page * 10:(page + 1) * 10]]
    navigation = []
    if page:
        navigation.append(InlineKeyboardButton(text=ru.PREVIOUS_BUTTON, callback_data=SessionStep(action="prev").pack()))
    if (page + 1) * 10 < len(players):
        navigation.append(InlineKeyboardButton(text=ru.NEXT_BUTTON, callback_data=SessionStep(action="next").pack()))
    if navigation:
        rows.append(navigation)
    rows.append([InlineKeyboardButton(text=ru.CANCEL_BUTTON, callback_data=SessionStep(action="cancel").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm(action: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=ru.CONFIRM_BUTTON, callback_data=SessionStep(action=action).pack())],
        [InlineKeyboardButton(text=ru.CANCEL_BUTTON, callback_data=SessionStep(action="cancel").pack())],
    ])
