"""Compact controls for recording a completed game."""

from uuid import UUID

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.callbacks import GameAction, GameAfter, GameChoice, GamePick, GameSave
from app.domain.enums import Team
from app.services.games import GamePerson, GameResult


def _button(label: str, action: str, flow: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=label, callback_data=GameAction(flow=flow, action=action).pack())


def participants(candidates: tuple[GamePerson, ...], selected: set[UUID], page: int, flow: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=("✅ " if p.id in selected else "▫️ ") + p.name,
              callback_data=GamePick(flow=flow, step="p", player_id=p.id).pack())]
            for p in candidates[page * 10:(page + 1) * 10]]
    navigation = []
    if page:
        navigation.append(_button("⬅️", "prev", flow))
    if (page + 1) * 10 < len(candidates):
        navigation.append(_button("➡️", "next", flow))
    if navigation:
        rows.append(navigation)
    rows.append([_button("✅ Состав выбран", "participants_done", flow)])
    rows.append([_button("❌ Отменить", "cancel", flow)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def hitler(players: tuple[GamePerson, ...], flow: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=p.name, callback_data=GamePick(flow=flow, step="h", player_id=p.id).pack())]
            for p in players]
    rows.append([_button("👥 Изменить состав", "participants", flow)])
    rows.append([_button("❌ Отменить", "cancel", flow)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def fascists(players: tuple[GamePerson, ...], selected: set[UUID], required: int, flow: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=("✅ " if p.id in selected else "▫️ ") + p.name,
              callback_data=GamePick(flow=flow, step="f", player_id=p.id).pack())]
            for p in players]
    if len(selected) == required:
        rows.append([_button("➡️ Продолжить", "fascists_done", flow)])
    rows.append([_button("⬅️ Назад", "hitler_back", flow), _button("❌ Отменить", "cancel", flow)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def winner(flow: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔵 Liberals", callback_data=GameChoice(flow=flow, kind="winner", code="L").pack())],
        [InlineKeyboardButton(text="🔴 Fascists", callback_data=GameChoice(flow=flow, kind="winner", code="F").pack())],
        [_button("⬅️ Назад", "fascists_back", flow), _button("❌ Отменить", "cancel", flow)],
    ])


def victory(team: Team, flow: str) -> InlineKeyboardMarkup:
    choices = (
        (("📘 5 либеральных законов", "LP"), ("🔫 Hitler убит", "HK"))
        if team is Team.LIBERAL else
        (("📕 6 фашистских законов", "FP"), ("👑 Hitler избран канцлером", "HC"))
    )
    return InlineKeyboardMarkup(inline_keyboard=[
        *[[InlineKeyboardButton(text=label, callback_data=GameChoice(flow=flow, kind="victory", code=code).pack())]
          for label, code in choices],
        [_button("⬅️ Назад", "winner_back", flow), _button("❌ Отменить", "cancel", flow)],
    ])


def review(draft_id: UUID, flow: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Сохранить", callback_data=GameSave(flow=flow, draft_id=draft_id).pack())],
        [_button("⬅️ Назад", "victory_back", flow), _button("❌ Отменить", "cancel", flow)],
    ])


def saved(game: GameResult, flow: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✨ Добавить момент", callback_data=GameAfter(flow=flow, game_id=game.id, action="m").pack())],
        [InlineKeyboardButton(text="🎲 Следующая игра", callback_data=GameAfter(flow=flow, game_id=game.id, action="n").pack())],
        [InlineKeyboardButton(text="🏠 К вечеру", callback_data=GameAfter(flow=flow, game_id=game.id, action="h").pack())],
    ])


def next_roster(game: GameResult, flow: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да", callback_data=GameAfter(flow=flow, game_id=game.id, action="s").pack())],
        [InlineKeyboardButton(text="👥 Изменился", callback_data=GameAfter(flow=flow, game_id=game.id, action="c").pack())],
        [InlineKeyboardButton(text="🏠 К вечеру", callback_data=GameAfter(flow=flow, game_id=game.id, action="h").pack())],
    ])
