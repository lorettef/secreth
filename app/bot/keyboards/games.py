"""Compact controls for recording a completed game."""

from uuid import UUID

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.callbacks import GameAction, GameAfter, GameChoice, GamePick, GameSave, SessionAction
from app.domain.enums import Team, VictoryType
from app.services.games import GamePerson, GameResult


def _button(label: str, action: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=label, callback_data=GameAction(action=action).pack())


def participants(candidates: tuple[GamePerson, ...], selected: set[UUID], page: int) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=("✅ " if p.id in selected else "▫️ ") + p.name,
              callback_data=GamePick(step="participants", player_id=p.id).pack())]
            for p in candidates[page * 10:(page + 1) * 10]]
    navigation = []
    if page:
        navigation.append(_button("⬅️", "prev"))
    if (page + 1) * 10 < len(candidates):
        navigation.append(_button("➡️", "next"))
    if navigation:
        rows.append(navigation)
    rows.append([_button("✅ Состав выбран", "participants_done")])
    rows.append([_button("❌ Отменить", "cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def hitler(players: tuple[GamePerson, ...]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=p.name, callback_data=GamePick(step="hitler", player_id=p.id).pack())]
            for p in players]
    rows.append([_button("👥 Изменить состав", "participants")])
    rows.append([_button("❌ Отменить", "cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def fascists(players: tuple[GamePerson, ...], selected: set[UUID], required: int) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=("✅ " if p.id in selected else "▫️ ") + p.name,
              callback_data=GamePick(step="fascists", player_id=p.id).pack())]
            for p in players]
    if len(selected) == required:
        rows.append([_button("➡️ Продолжить", "fascists_done")])
    rows.append([_button("⬅️ Назад", "hitler_back"), _button("❌ Отменить", "cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def winner() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔵 Liberals", callback_data=GameChoice(kind="winner", code="L").pack())],
        [InlineKeyboardButton(text="🔴 Fascists", callback_data=GameChoice(kind="winner", code="F").pack())],
        [_button("⬅️ Назад", "fascists_back"), _button("❌ Отменить", "cancel")],
    ])


def victory(team: Team) -> InlineKeyboardMarkup:
    choices = (
        (("📘 5 либеральных законов", "LP"), ("🔫 Hitler убит", "HK"))
        if team is Team.LIBERAL else
        (("📕 6 фашистских законов", "FP"), ("👑 Hitler избран канцлером", "HC"))
    )
    return InlineKeyboardMarkup(inline_keyboard=[
        *[[InlineKeyboardButton(text=label, callback_data=GameChoice(kind="victory", code=code).pack())]
          for label, code in choices],
        [_button("⬅️ Назад", "winner_back"), _button("❌ Отменить", "cancel")],
    ])


def review(draft_id: UUID) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Сохранить", callback_data=GameSave(draft_id=draft_id).pack())],
        [_button("⬅️ Назад", "victory_back"), _button("❌ Отменить", "cancel")],
    ])


def saved(game: GameResult) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✨ Добавить момент", callback_data=GameAfter(game_id=game.id, action="moment").pack())],
        [InlineKeyboardButton(text="🎲 Следующая игра", callback_data=GameAfter(game_id=game.id, action="next").pack())],
        [InlineKeyboardButton(text="🏠 К вечеру", callback_data=GameAfter(game_id=game.id, action="home").pack())],
    ])


def next_roster(game: GameResult) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да", callback_data=GameAfter(game_id=game.id, action="same").pack())],
        [InlineKeyboardButton(text="👥 Изменился", callback_data=GameAfter(game_id=game.id, action="changed").pack())],
        [InlineKeyboardButton(text="🏠 К вечеру", callback_data=SessionAction(
            session_id=game.session_id, action="view").pack())],
    ])
