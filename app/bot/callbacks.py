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


class SessionPick(CallbackData, prefix="sp"):
    player_id: UUID


class SessionStep(CallbackData, prefix="step"):
    action: str
