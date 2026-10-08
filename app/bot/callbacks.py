"""Compact, structured callback payloads for onboarding."""

from uuid import UUID

from aiogram.filters.callback_data import CallbackData


class JoinInvite(CallbackData, prefix="join"):
    invite_id: UUID


class ClubAction(CallbackData, prefix="club"):
    club_id: UUID
    action: str


class PlayerPage(CallbackData, prefix="players"):
    club_id: UUID
    page: int


class ChoosePlayer(CallbackData, prefix="choose"):
    player_id: UUID


class ReviewPage(CallbackData, prefix="reviews"):
    club_id: UUID
    page: int


class ReviewLink(CallbackData, prefix="review"):
    request_id: UUID
    action: str


class SessionAction(CallbackData, prefix="sess"):
    session_id: UUID
    action: str


class SessionStart(CallbackData, prefix="ss"):
    club_id: UUID


class SessionPick(CallbackData, prefix="sp2"):
    flow: str
    player_id: UUID


class SessionStep(CallbackData, prefix="step2"):
    flow: str
    action: str


class RecoveryAction(CallbackData, prefix="rec"):
    request_id: UUID
    action: str


class GameAction(CallbackData, prefix="ga2"):
    flow: str
    action: str


class GamePick(CallbackData, prefix="gp2"):
    flow: str
    # p/h/f keep UUID-bearing callbacks below Telegram's 64-byte limit.
    step: str
    player_id: UUID


class GameChoice(CallbackData, prefix="gc2"):
    flow: str
    kind: str
    code: str


class GameSave(CallbackData, prefix="gs2"):
    flow: str
    draft_id: UUID


class GameAfter(CallbackData, prefix="gf2"):
    flow: str
    game_id: UUID
    # m/n/h/s/c: moment/next/home/same/changed.
    action: str


class GameCard(CallbackData, prefix="gcard"):
    game_id: UUID


class EventStart(CallbackData, prefix="estart"):
    game_id: UUID


class EventChoice(CallbackData, prefix="ec"):
    flow: str
    kind: str
    code: str


class EventPick(CallbackData, prefix="ep"):
    flow: str
    player_id: UUID


class EventAction(CallbackData, prefix="ea"):
    flow: str
    action: str


class EventSave(CallbackData, prefix="es"):
    flow: str
    draft_id: UUID


class EventAfter(CallbackData, prefix="ef"):
    flow: str
    event_id: UUID
    action: str


class StatsPage(CallbackData, prefix="stat"):
    club_id: UUID
    section: str
    page: int = 0


class StatsPlayer(CallbackData, prefix="statp"):
    player_id: UUID


class StatsSession(CallbackData, prefix="stats"):
    session_id: UUID
    page: int = 0


class HistoryPage(CallbackData, prefix="hp"):
    scope_id: UUID
    section: str
    page: int = 0


class HistoryMutation(CallbackData, prefix="hm"):
    entity_id: UUID
    action: str
    revision: str


class HistoryStep(CallbackData, prefix="hs"):
    flow: str
    action: str


class HistoryPick(CallbackData, prefix="hi"):
    flow: str
    player_id: UUID
