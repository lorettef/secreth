"""Read-only identity projection shared by HTTP transports."""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Player
from app.domain.enums import MembershipRole, MembershipStatus
from app.domain.permissions import can_view_club
from app.services.access import resolve_actor


class HttpIdentityForbidden(PermissionError):
    pass


class RegistrationState(StrEnum):
    NOT_MEMBER = "NOT_MEMBER"
    PROFILE_REQUIRED = "PROFILE_REQUIRED"
    REGISTERED = "REGISTERED"


@dataclass(frozen=True)
class ProfileView:
    id: UUID
    display_name: str
    first_name: str | None
    last_name: str | None
    school_nickname: str | None


@dataclass(frozen=True)
class IdentityView:
    telegram_id: int
    registration_state: RegistrationState
    club_id: UUID | None = None
    membership_id: UUID | None = None
    membership_role: MembershipRole | None = None
    membership_status: MembershipStatus | None = None
    profile: ProfileView | None = None


class HttpIdentityService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def me(self, telegram_id: int, club_id: UUID) -> IdentityView:
        async with self._session_factory() as session:
            actor = await resolve_actor(session, telegram_id, club_id)
            if actor.membership_status is MembershipStatus.BLOCKED:
                raise HttpIdentityForbidden
            if actor.membership_id is None:
                return IdentityView(
                    telegram_id=telegram_id,
                    registration_state=RegistrationState.NOT_MEMBER,
                )
            if not can_view_club(actor, club_id):
                raise HttpIdentityForbidden
            player = (
                await session.scalar(
                    select(Player).where(
                        Player.id == actor.player_id,
                        Player.club_id == club_id,
                    )
                )
                if actor.player_id is not None
                else None
            )
            profile = None
            state = RegistrationState.PROFILE_REQUIRED
            if player is not None:
                profile = ProfileView(
                    id=player.id,
                    display_name=player.display_name,
                    first_name=player.first_name,
                    last_name=player.last_name,
                    school_nickname=player.school_nickname,
                )
                if player.first_name and player.last_name and player.school_nickname:
                    state = RegistrationState.REGISTERED
            return IdentityView(
                telegram_id=telegram_id,
                registration_state=state,
                club_id=club_id,
                membership_id=actor.membership_id,
                membership_role=actor.membership_role,
                membership_status=actor.membership_status,
                profile=profile,
            )
