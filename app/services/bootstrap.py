"""Operator-only initial club, invitation, and owner setup."""

import uuid
from dataclasses import dataclass
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    AuditLog,
    Club,
    ClubMembership,
    InviteToken,
    TelegramUser,
)
from app.domain.enums import MembershipRole, MembershipStatus
from app.services.invites import generate_invite_token


class BootstrapErrorCode(StrEnum):
    CLUB_NOT_FOUND = "CLUB_NOT_FOUND"
    CLUB_NAME_REQUIRED = "CLUB_NAME_REQUIRED"
    INVALID_TIMEZONE = "INVALID_TIMEZONE"
    OWNER_ALREADY_ASSIGNED = "OWNER_ALREADY_ASSIGNED"
    BOOTSTRAP_INVITE_EXISTS = "BOOTSTRAP_INVITE_EXISTS"
    TARGET_NOT_MEMBER = "TARGET_NOT_MEMBER"
    TARGET_BLOCKED = "TARGET_BLOCKED"


class BootstrapError(ValueError):
    def __init__(self, code: BootstrapErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True)
class OwnerAssignment:
    membership_id: uuid.UUID
    changed: bool


class ClubBootstrapService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_club(self, name: str, timezone_name: str) -> uuid.UUID:
        normalized_name = name.strip()
        if not normalized_name:
            raise BootstrapError(BootstrapErrorCode.CLUB_NAME_REQUIRED)
        try:
            ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise BootstrapError(BootstrapErrorCode.INVALID_TIMEZONE) from exc

        async with self._session_factory() as session, session.begin():
            club = Club(name=normalized_name[:255], timezone=timezone_name)
            session.add(club)
            await session.flush()
            session.add(
                AuditLog(
                    club_id=club.id,
                    actor_player_id=None,
                    entity_type="Club",
                    entity_id=club.id,
                    action="BOOTSTRAP_CLUB_CREATE",
                    after_data={
                        "origin": "SYSTEM_CLI",
                        "name": club.name,
                        "timezone": club.timezone,
                    },
                )
            )
            return club.id

    async def create_initial_invite(self, club_id: uuid.UUID) -> str:
        async with self._session_factory() as session, session.begin():
            club = await self._lock_club(session, club_id)
            if await self._owner(session, club.id) is not None:
                raise BootstrapError(BootstrapErrorCode.OWNER_ALREADY_ASSIGNED)
            existing = await session.scalar(
                select(InviteToken.id).where(
                    InviteToken.club_id == club.id,
                    InviteToken.is_bootstrap.is_(True),
                )
            )
            if existing is not None:
                raise BootstrapError(BootstrapErrorCode.BOOTSTRAP_INVITE_EXISTS)

            invitation = InviteToken(
                id=uuid.uuid4(),
                club_id=club.id,
                token=generate_invite_token(),
                created_by=None,
                is_bootstrap=True,
                is_active=True,
                expires_at=None,
                max_uses=1,
            )
            session.add(invitation)
            session.add(
                AuditLog(
                    club_id=club.id,
                    actor_player_id=None,
                    entity_type="InviteToken",
                    entity_id=invitation.id,
                    action="BOOTSTRAP_INVITE_CREATE",
                    after_data={
                        "origin": "SYSTEM_CLI",
                        "max_uses": 1,
                        "expires_at": None,
                    },
                )
            )
            await session.flush()
            return invitation.token

    async def assign_owner(
        self, club_id: uuid.UUID, telegram_id: int
    ) -> OwnerAssignment:
        async with self._session_factory() as session, session.begin():
            club = await self._lock_club(session, club_id)
            target = await session.scalar(
                select(ClubMembership)
                .join(TelegramUser, TelegramUser.id == ClubMembership.telegram_user_id)
                .where(
                    ClubMembership.club_id == club.id,
                    TelegramUser.telegram_user_id == telegram_id,
                )
                .with_for_update()
            )
            if target is None:
                raise BootstrapError(BootstrapErrorCode.TARGET_NOT_MEMBER)

            owner = await self._owner(session, club.id)
            if owner is not None:
                if owner.id == target.id:
                    return OwnerAssignment(target.id, changed=False)
                raise BootstrapError(BootstrapErrorCode.OWNER_ALREADY_ASSIGNED)
            if target.status is not MembershipStatus.ACTIVE:
                raise BootstrapError(BootstrapErrorCode.TARGET_BLOCKED)

            previous_role = target.role
            target.role = MembershipRole.OWNER
            session.add(
                AuditLog(
                    club_id=club.id,
                    actor_player_id=None,
                    entity_type="ClubMembership",
                    entity_id=target.id,
                    action="BOOTSTRAP_OWNER_ASSIGN",
                    before_data={"role": previous_role.value},
                    after_data={
                        "origin": "SYSTEM_CLI",
                        "role": MembershipRole.OWNER.value,
                    },
                )
            )
            await session.flush()
            return OwnerAssignment(target.id, changed=True)

    @staticmethod
    async def _lock_club(session: AsyncSession, club_id: uuid.UUID) -> Club:
        club = await session.scalar(
            select(Club).where(Club.id == club_id).with_for_update()
        )
        if club is None:
            raise BootstrapError(BootstrapErrorCode.CLUB_NOT_FOUND)
        return club

    @staticmethod
    async def _owner(
        session: AsyncSession, club_id: uuid.UUID
    ) -> ClubMembership | None:
        return await session.scalar(
            select(ClubMembership).where(
                ClubMembership.club_id == club_id,
                ClubMembership.role == MembershipRole.OWNER,
            )
        )
