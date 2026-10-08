"""Event buttons bound to a single screen; compact UUID callbacks."""

from uuid import UUID
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.callbacks import EventAction, EventAfter, EventChoice, EventPick, EventSave, EventStart, SessionAction
from app.locales import ru
from app.services.games import GamePerson


def _action(label: str, action: str, flow: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=label, callback_data=EventAction(flow=flow, action=action).pack())


def _navigation(flow: str, back: bool = True) -> list[InlineKeyboardButton]:
    return ([_action(ru.BACK_BUTTON, "back", flow)] if back else []) + [_action(ru.CANCEL_BUTTON, "cancel", flow)]


def choices(labels: dict[str, str], kind: str, flow: str, *, back: bool = True) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        *[[InlineKeyboardButton(text=label, callback_data=EventChoice(flow=flow, kind=kind, code=code).pack())]
          for code, label in labels.items()], _navigation(flow, back),
    ])


def players(people: tuple[GamePerson, ...], flow: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        *[[InlineKeyboardButton(text=p.name, callback_data=EventPick(flow=flow, player_id=p.id).pack())] for p in people],
        _navigation(flow),
    ])


def comment(flow: str, *, allow_clear: bool = False) -> InlineKeyboardMarkup:
    rows = [[_action(ru.EVENT_KEEP_COMMENT if allow_clear else ru.EVENT_SKIP, "skip", flow)]]
    if allow_clear:
        rows.append([_action(ru.EVENT_CLEAR_COMMENT, "clear", flow)])
    return InlineKeyboardMarkup(inline_keyboard=[*rows, _navigation(flow)])


def review(draft_id: UUID, flow: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=ru.EVENT_SAVE, callback_data=EventSave(flow=flow, draft_id=draft_id).pack())],
        _navigation(flow),
    ])


def saved(event_id: UUID, flow: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=EventAfter(flow=flow, event_id=event_id, action=code).pack())]
        for label, code in ((ru.EVENT_MORE, "m"), (ru.EVENT_GAME, "g"), (ru.EVENT_HOME, "h"))
    ])


def game_card(game_id: UUID, session_id: UUID, can_create: bool) -> InlineKeyboardMarkup:
    rows = []
    if can_create:
        rows.append([InlineKeyboardButton(text=ru.EVENT_ADD, callback_data=EventStart(game_id=game_id).pack())])
    rows.append([InlineKeyboardButton(text=ru.EVENT_HOME,
                callback_data=SessionAction(session_id=session_id, action="view").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)
