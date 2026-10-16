"""Invitation, membership, and historical player linking workflows."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.exc import IntegrityError

from app.db.models import (
    AuditLog,
    Club,
    ClubMembership,
    InviteToken,
    Player,
    PlayerLinkRequest,
    TelegramUser,
)
from app.domain.enums import MembershipRole, MembershipStatus, PlayerLinkStatus
from app.domain.permissions import ActorContext, can_change_member_status, can_manage_club
from app.domain.profiles import MAX_PROFILE_FIELD_LENGTH, normalize_profile_text
from app.services.invites import generate_invite_token


class MembershipErrorCode(StrEnum):
    USER_NOT_FOUND = "USER_NOT_FOUND"
    NOT_MEMBER = "NOT_MEMBER"
    BLOCKED = "BLOCKED"
    NOT_ADMIN = "NOT_ADMIN"
    ADMIN_PLAYER_REQUIRED = "ADMIN_PLAYER_REQUIRED"
    INVITE_INVALID = "INVITE_INVALID"
    INVITE_EXPIRED = "INVITE_EXPIRED"
    INVITE_EXHAUSTED = "INVITE_EXHAUSTED"
    INVALID_INVITE_OPTIONS = "INVALID_INVITE_OPTIONS"
    ALREADY_LINKED = "ALREADY_LINKED"
    NAME_REQUIRED = "NAME_REQUIRED"
    FIRST_NAME_REQUIRED = "FIRST_NAME_REQUIRED"
    LAST_NAME_REQUIRED = "LAST_NAME_REQUIRED"
    SCHOOL_NICKNAME_REQUIRED = "SCHOOL_NICKNAME_REQUIRED"
    PROFILE_FIELD_TOO_LONG = "PROFILE_FIELD_TOO_LONG"
    SCHOOL_NICKNAME_CONFLICT = "SCHOOL_NICKNAME_CONFLICT"
    PROFILE_NOT_FOUND = "PROFILE_NOT_FOUND"
    PENDING_EXISTS = "PENDING_EXISTS"
    PLAYER_NOT_AVAILABLE = "PLAYER_NOT_AVAILABLE"
    REQUEST_NOT_FOUND = "REQUEST_NOT_FOUND"
    ALREADY_REVIEWED = "ALREADY_REVIEWED"
    INACTIVE = "INACTIVE"
    INVALID_STATUS = "INVALID_STATUS"
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    TARGET_ROLE_FORBIDDEN = "TARGET_ROLE_FORBIDDEN"


class MembershipError(ValueError):
    def __init__(self, code: MembershipErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True)
class TelegramProfile:
    telegram_id: int
    username: str | None
    first_name: str
    last_name: str | None


@dataclass(frozen=True)
class ClubView:
    id: uuid.UUID
    name: str
    role: MembershipRole
    player_name: str | None
    has_pending_link: bool
    status: MembershipStatus = MembershipStatus.ACTIVE


@dataclass(frozen=True)
class InviteView:
    id: uuid.UUID
    club_name: str


@dataclass(frozen=True)
class PlayerChoice:
    id: uuid.UUID
    display_name: str


@dataclass(frozen=True)
class PendingLinkView:
    id: uuid.UUID
    player_name: str
    requester_name: str
    requester_username: str | None
    requester_telegram_id: int


@dataclass(frozen=True)
class PlayerProfileView:
    id: uuid.UUID
    club_id: uuid.UUID
    first_name: str
    last_name: str
    school_nickname: str
    display_name: str


@dataclass(frozen=True)
class MemberStatusChange:
    membership_id: uuid.UUID
    player_id: uuid.UUID
    status: MembershipStatus
    changed: bool


@dataclass(frozen=True)
class _ProfileValues:
    first_name: str
    last_name: str
    school_nickname: str
    display_name: str


class MembershipService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def register_telegram_user(self, profile: TelegramProfile) -> uuid.UUID:
        """Refresh Telegram identity without granting club access."""
        async with self._session_factory() as session, session.begin():
            statement = (
                insert(TelegramUser)
                .values(
                    id=uuid.uuid4(),
                    telegram_user_id=profile.telegram_id,
                    username=profile.username,
                    first_name=profile.first_name,
                    last_name=profile.last_name,
                )
                .on_conflict_do_update(
                    index_elements=[TelegramUser.telegram_user_id],
                    set_={
                        "username": profile.username,
                        "first_name": profile.first_name,
                        "last_name": profile.last_name,
                        "updated_at": func.now(),
                    },
                )
                .returning(TelegramUser.id)
            )
            return (await session.execute(statement)).scalar_one()

    async def list_clubs(self, telegram_id: int) -> tuple[ClubView, ...]:
        async with self._session_factory() as session:
            user = await self._get_user(session, telegram_id)
            memberships = (
                await session.scalars(
                    select(ClubMembership)
                    .where(
                        ClubMembership.telegram_user_id == user.id,
                        ClubMembership.status != MembershipStatus.BLOCKED,
                    )
                    .order_by(ClubMembership.created_at)
                )
            ).all()
            return tuple(
                [await self._club_view(session, user, membership) for membership in memberships]
            )

    async def get_club(self, telegram_id: int, club_id: uuid.UUID) -> ClubView:
        async with self._session_factory() as session:
            user = await self._get_user(session, telegram_id)
            membership = await self._require_member(session, user.id, club_id)
            return await self._club_view(session, user, membership)

    async def get_admin_club(self, telegram_id: int, club_id: uuid.UUID) -> ClubView:
        async with self._session_factory() as session:
            user = await self._get_user(session, telegram_id)
            await self._require_admin(session, user.id, club_id)
            membership = await self._require_member(session, user.id, club_id)
            return await self._club_view(session, user, membership)

    async def preview_invite(self, token: str) -> InviteView:
        async with self._session_factory() as session:
            invitation = await session.scalar(select(InviteToken).where(InviteToken.token == token))
            self._validate_invite(invitation)
            club = await session.get(Club, invitation.club_id)
            return InviteView(invitation.id, club.name)

    async def join_invite(self, telegram_id: int, invite_id: uuid.UUID) -> ClubView:
        async with self._session_factory() as session, session.begin():
            user = await self._get_user(session, telegram_id, lock=True)
            invitation = await session.scalar(
                select(InviteToken).where(InviteToken.id == invite_id).with_for_update()
            )
            if invitation is None:
                raise MembershipError(MembershipErrorCode.INVITE_INVALID)
            membership = await session.scalar(
                select(ClubMembership).where(
                    ClubMembership.club_id == invitation.club_id,
                    ClubMembership.telegram_user_id == user.id,
                )
            )
            if membership is not None:
                if membership.status is MembershipStatus.BLOCKED:
                    raise MembershipError(MembershipErrorCode.BLOCKED)
                return await self._club_view(session, user, membership)
            self._validate_invite(invitation)
            membership = ClubMembership(
                club_id=invitation.club_id,
                telegram_user_id=user.id,
                role=MembershipRole.PLAYER,
                status=MembershipStatus.ACTIVE,
            )
            session.add(membership)
            invitation.uses_count += 1
            await session.flush()
            return await self._club_view(session, user, membership)

    async def create_new_player(self, telegram_id: int, club_id: uuid.UUID) -> ClubView:
        async with self._session_factory() as session, session.begin():
            user = await self._get_user(session, telegram_id, lock=True)
            membership = await self._require_active_member(session, user.id, club_id)
            if await self._bound_player(session, user.id, club_id):
                raise MembershipError(MembershipErrorCode.ALREADY_LINKED)
            if await self._pending_for_user(session, user.id, club_id):
                raise MembershipError(MembershipErrorCode.PENDING_EXISTS)
            display_name = (user.first_name or user.username or "").strip()
            if not display_name:
                raise MembershipError(MembershipErrorCode.NAME_REQUIRED)
            session.add(
                Player(club_id=club_id, display_name=display_name[:255], telegram_user_id=user.id)
            )
            await session.flush()
            return await self._club_view(session, user, membership)

    async def create_member_profile(
        self,
        telegram_id: int,
        club_id: uuid.UUID,
        *,
        first_name: str,
        last_name: str,
        school_nickname: str,
    ) -> PlayerProfileView:
        """Create a new profile for an active member without historical matching."""

        try:
            async with self._session_factory() as session, session.begin():
                user = await self._get_user(session, telegram_id, lock=True)
                await self._require_active_member(session, user.id, club_id)
                if await self._bound_player(session, user.id, club_id):
                    raise MembershipError(MembershipErrorCode.ALREADY_LINKED)
                if await self._pending_for_user(session, user.id, club_id):
                    raise MembershipError(MembershipErrorCode.PENDING_EXISTS)
                values = self._profile_values(
                    first_name, last_name, school_nickname
                )
                player = Player(
                    club_id=club_id,
                    telegram_user_id=user.id,
                    display_name=values.display_name,
                    first_name=values.first_name,
                    last_name=values.last_name,
                    school_nickname=values.school_nickname,
                )
                session.add(player)
                await session.flush()
                return self._profile(player)
        except IntegrityError as error:
            self._raise_profile_integrity_error(error)

    async def update_member_profile(
        self,
        telegram_id: int,
        club_id: uuid.UUID,
        *,
        first_name: str,
        last_name: str,
        school_nickname: str,
    ) -> PlayerProfileView:
        """Replace the caller's club-scoped profile fields atomically."""

        try:
            async with self._session_factory() as session, session.begin():
                user = await self._get_user(session, telegram_id)
                await self._require_active_member(session, user.id, club_id)
                player = await session.scalar(
                    select(Player)
                    .where(
                        Player.club_id == club_id,
                        Player.telegram_user_id == user.id,
                    )
                    .with_for_update()
                )
                if player is None:
                    raise MembershipError(MembershipErrorCode.PROFILE_NOT_FOUND)
                values = self._profile_values(
                    first_name, last_name, school_nickname
                )
                player.first_name = values.first_name
                player.last_name = values.last_name
                player.school_nickname = values.school_nickname
                player.display_name = values.display_name
                await session.flush()
                return self._profile(player)
        except IntegrityError as error:
            self._raise_profile_integrity_error(error)

    async def list_unlinked_players(
        self, telegram_id: int, club_id: uuid.UUID, *, page: int = 0, page_size: int = 10
    ) -> tuple[PlayerChoice, ...]:
        async with self._session_factory() as session:
            user = await self._get_user(session, telegram_id)
            await self._require_active_member(session, user.id, club_id)
            if await self._bound_player(session, user.id, club_id):
                raise MembershipError(MembershipErrorCode.ALREADY_LINKED)
            if await self._pending_for_user(session, user.id, club_id):
                raise MembershipError(MembershipErrorCode.PENDING_EXISTS)
            players = (
                await session.scalars(
                    select(Player)
                    .where(Player.club_id == club_id, Player.telegram_user_id.is_(None))
                    .order_by(Player.display_name, Player.id)
                    .offset(max(page, 0) * page_size)
                    .limit(page_size)
                )
            ).all()
            return tuple(PlayerChoice(player.id, player.display_name) for player in players)

    async def request_player_link(self, telegram_id: int, player_id: uuid.UUID) -> uuid.UUID:
        async with self._session_factory() as session, session.begin():
            user = await self._get_user(session, telegram_id, lock=True)
            player = await session.scalar(select(Player).where(Player.id == player_id).with_for_update())
            if player is None or player.telegram_user_id is not None:
                raise MembershipError(MembershipErrorCode.PLAYER_NOT_AVAILABLE)
            await self._require_active_member(session, user.id, player.club_id)
            if await self._bound_player(session, user.id, player.club_id):
                raise MembershipError(MembershipErrorCode.ALREADY_LINKED)
            existing = await self._pending_for_user(session, user.id, player.club_id)
            if existing is not None:
                if existing.player_id == player.id:
                    return existing.id
                raise MembershipError(MembershipErrorCode.PENDING_EXISTS)
            if await session.scalar(
                select(PlayerLinkRequest.id).where(
                    PlayerLinkRequest.player_id == player.id,
                    PlayerLinkRequest.status == PlayerLinkStatus.PENDING,
                )
            ):
                raise MembershipError(MembershipErrorCode.PLAYER_NOT_AVAILABLE)
            request = PlayerLinkRequest(
                club_id=player.club_id,
                telegram_user_id=user.id,
                player_id=player.id,
                status=PlayerLinkStatus.PENDING,
            )
            session.add(request)
            await session.flush()
            return request.id

    async def create_invite(
        self,
        admin_telegram_id: int,
        club_id: uuid.UUID,
        *,
        expires_at: datetime | None = None,
        max_uses: int | None = None,
    ) -> str:
        if (expires_at is not None and (expires_at.tzinfo is None or expires_at <= datetime.now(timezone.utc))) or (
            max_uses is not None and max_uses < 1
        ):
            raise MembershipError(MembershipErrorCode.INVALID_INVITE_OPTIONS)
        async with self._session_factory() as session, session.begin():
            user = await self._get_user(session, admin_telegram_id)
            admin_player = await self._require_admin(session, user.id, club_id)
            if admin_player is None:
                raise MembershipError(MembershipErrorCode.ADMIN_PLAYER_REQUIRED)
            token = generate_invite_token()
            session.add(
                InviteToken(
                    club_id=club_id,
                    token=token,
                    created_by=admin_player.id,
                    is_active=True,
                    expires_at=expires_at,
                    max_uses=max_uses,
                )
            )
            await session.flush()
            return token

    async def list_pending_links(
        self, admin_telegram_id: int, club_id: uuid.UUID, *, page: int = 0, limit: int = 10
    ) -> tuple[PendingLinkView, ...]:
        async with self._session_factory() as session:
            user = await self._get_user(session, admin_telegram_id)
            await self._require_admin(session, user.id, club_id)
            rows = (
                await session.execute(
                    select(
                        PlayerLinkRequest.id,
                        Player.display_name,
                        TelegramUser.first_name,
                        TelegramUser.username,
                        TelegramUser.telegram_user_id,
                    )
                    .join(Player, Player.id == PlayerLinkRequest.player_id)
                    .join(TelegramUser, TelegramUser.id == PlayerLinkRequest.telegram_user_id)
                    .where(
                        PlayerLinkRequest.club_id == club_id,
                        PlayerLinkRequest.status == PlayerLinkStatus.PENDING,
                    )
                    .order_by(PlayerLinkRequest.created_at)
                    .offset(max(page, 0) * limit)
                    .limit(limit)
                )
            ).all()
            return tuple(
                PendingLinkView(
                    row.id,
                    row.display_name,
                    row.first_name or "",
                    row.username,
                    row.telegram_user_id,
                )
                for row in rows
            )

    async def get_pending_link(
        self, admin_telegram_id: int, request_id: uuid.UUID
    ) -> tuple[uuid.UUID, PendingLinkView]:
        async with self._session_factory() as session:
            user = await self._get_user(session, admin_telegram_id)
            request = await session.get(PlayerLinkRequest, request_id)
            if request is None:
                raise MembershipError(MembershipErrorCode.REQUEST_NOT_FOUND)
            await self._require_admin(session, user.id, request.club_id)
            if request.status is not PlayerLinkStatus.PENDING:
                raise MembershipError(MembershipErrorCode.ALREADY_REVIEWED)
            player = await session.get(Player, request.player_id)
            requester = await session.get(TelegramUser, request.telegram_user_id)
            return request.club_id, PendingLinkView(
                request.id,
                player.display_name,
                requester.first_name or "",
                requester.username,
                requester.telegram_user_id,
            )

    async def review_player_link(
        self, admin_telegram_id: int, request_id: uuid.UUID, *, approve: bool
    ) -> PlayerLinkStatus:
        async with self._session_factory() as session, session.begin():
            admin_user = await self._get_user(session, admin_telegram_id)
            request = await session.scalar(
                select(PlayerLinkRequest)
                .where(PlayerLinkRequest.id == request_id)
                .with_for_update()
            )
            if request is None:
                raise MembershipError(MembershipErrorCode.REQUEST_NOT_FOUND)
            admin_player = await self._require_admin(session, admin_user.id, request.club_id)
            if request.status is not PlayerLinkStatus.PENDING:
                raise MembershipError(MembershipErrorCode.ALREADY_REVIEWED)
            if approve:
                target_user = await session.scalar(
                    select(TelegramUser)
                    .where(TelegramUser.id == request.telegram_user_id)
                    .with_for_update()
                )
                await self._require_active_member(session, target_user.id, request.club_id)
                player = await session.scalar(
                    select(Player).where(Player.id == request.player_id).with_for_update()
                )
                if player.club_id != request.club_id or player.telegram_user_id is not None:
                    raise MembershipError(MembershipErrorCode.PLAYER_NOT_AVAILABLE)
                if await self._bound_player(session, target_user.id, request.club_id):
                    raise MembershipError(MembershipErrorCode.ALREADY_LINKED)
                was_active = player.is_active
                player.telegram_user_id = target_user.id
                player.is_active = True
                session.add(
                    AuditLog(
                        club_id=request.club_id,
                        actor_player_id=admin_player.id if admin_player else None,
                        entity_type="Player",
                        entity_id=player.id,
                        action="PLAYER_LINK",
                        before_data={"telegram_user_id": None, "is_active": was_active},
                        after_data={"telegram_user_id": str(target_user.id), "is_active": True},
                    )
                )
            request.status = PlayerLinkStatus.APPROVED if approve else PlayerLinkStatus.REJECTED
            request.reviewed_at = datetime.now(timezone.utc)
            request.reviewed_by_player_id = admin_player.id if admin_player else None
            await session.flush()
            return request.status

    async def change_member_status(
        self, actor_telegram_id: int, club_id: uuid.UUID,
        target_player_id: uuid.UUID, status: MembershipStatus,
    ) -> MemberStatusChange:
        """Change a registered member's canonical status and legacy projection."""
        if not isinstance(status, MembershipStatus):
            raise MembershipError(MembershipErrorCode.INVALID_STATUS)
        async with self._session_factory() as session, session.begin():
            actor_user = await self._get_user(session, actor_telegram_id)
            actor_membership = await self._require_active_member(
                session, actor_user.id, club_id, lock=True
            )
            actor_player = await self._bound_player(session, actor_user.id, club_id)
            actor = ActorContext(
                club_id=club_id, membership_role=actor_membership.role,
                membership_status=actor_membership.status,
                player_id=actor_player.id if actor_player else None,
                player_is_active=actor_player is not None, telegram_id=actor_telegram_id,
                telegram_user_id=actor_user.id, membership_id=actor_membership.id,
            )
            if not can_manage_club(actor, club_id):
                raise MembershipError(MembershipErrorCode.NOT_ADMIN)
            target = await session.scalar(select(Player).where(
                Player.id == target_player_id, Player.club_id == club_id).with_for_update())
            if target is None or target.telegram_user_id is None:
                raise MembershipError(MembershipErrorCode.TARGET_NOT_FOUND)
            target_membership = await session.scalar(select(ClubMembership).where(
                ClubMembership.club_id == club_id,
                ClubMembership.telegram_user_id == target.telegram_user_id,
            ).with_for_update())
            if target_membership is None:
                raise MembershipError(MembershipErrorCode.TARGET_NOT_FOUND)
            if not can_change_member_status(actor, club_id, target_membership.role):
                raise MembershipError(MembershipErrorCode.TARGET_ROLE_FORBIDDEN)
            if target_membership.status is status:
                return MemberStatusChange(target_membership.id, target.id, status, False)
            before = {"status": target_membership.status.value}
            target_membership.status = status
            target.is_active = status is MembershipStatus.ACTIVE
            session.add(AuditLog(
                club_id=club_id, actor_player_id=actor.player_id,
                entity_type="ClubMembership", entity_id=target_membership.id,
                action="MEMBER_STATUS_CHANGE", before_data=before,
                after_data={"status": status.value, "actor_telegram_id": actor_telegram_id},
            ))
            await session.flush()
            return MemberStatusChange(target_membership.id, target.id, status, True)

    @staticmethod
    async def _get_user(
        session: AsyncSession, telegram_id: int, *, lock: bool = False
    ) -> TelegramUser:
        statement = select(TelegramUser).where(TelegramUser.telegram_user_id == telegram_id)
        if lock:
            statement = statement.with_for_update()
        user = await session.scalar(statement)
        if user is None:
            raise MembershipError(MembershipErrorCode.USER_NOT_FOUND)
        return user

    @staticmethod
    async def _require_member(
        session: AsyncSession, user_id: uuid.UUID, club_id: uuid.UUID,
        *, lock: bool = False,
    ) -> ClubMembership:
        statement = select(ClubMembership).where(
            ClubMembership.club_id == club_id,
            ClubMembership.telegram_user_id == user_id,
        )
        if lock:
            statement = statement.with_for_update()
        membership = await session.scalar(statement)
        if membership is None:
            raise MembershipError(MembershipErrorCode.NOT_MEMBER)
        if membership.status is MembershipStatus.BLOCKED:
            raise MembershipError(MembershipErrorCode.BLOCKED)
        return membership

    @classmethod
    async def _require_active_member(
        cls, session: AsyncSession, user_id: uuid.UUID, club_id: uuid.UUID,
        *, lock: bool = False,
    ) -> ClubMembership:
        membership = await cls._require_member(session, user_id, club_id, lock=lock)
        if membership.status is MembershipStatus.INACTIVE:
            raise MembershipError(MembershipErrorCode.INACTIVE)
        return membership

    async def _require_admin(
        self, session: AsyncSession, user_id: uuid.UUID, club_id: uuid.UUID
    ) -> Player | None:
        membership = await self._require_active_member(session, user_id, club_id)
        player = await self._bound_player(session, user_id, club_id)
        actor = ActorContext(
            club_id=club_id,
            membership_role=membership.role,
            membership_status=membership.status,
            player_id=player.id if player else None,
            player_is_active=player is not None,
        )
        if not can_manage_club(actor, club_id):
            raise MembershipError(MembershipErrorCode.NOT_ADMIN)
        return player

    @staticmethod
    async def _bound_player(
        session: AsyncSession, user_id: uuid.UUID, club_id: uuid.UUID
    ) -> Player | None:
        return await session.scalar(
            select(Player).where(Player.club_id == club_id, Player.telegram_user_id == user_id)
        )

    @staticmethod
    async def _pending_for_user(
        session: AsyncSession, user_id: uuid.UUID, club_id: uuid.UUID
    ) -> PlayerLinkRequest | None:
        return await session.scalar(
            select(PlayerLinkRequest).where(
                PlayerLinkRequest.club_id == club_id,
                PlayerLinkRequest.telegram_user_id == user_id,
                PlayerLinkRequest.status == PlayerLinkStatus.PENDING,
            )
        )

    async def _club_view(
        self, session: AsyncSession, user: TelegramUser, membership: ClubMembership
    ) -> ClubView:
        club = await session.get(Club, membership.club_id)
        player = await self._bound_player(session, user.id, membership.club_id)
        pending = await self._pending_for_user(session, user.id, membership.club_id)
        return ClubView(club.id, club.name, membership.role,
                        player.display_name if player else None, pending is not None,
                        membership.status)

    @staticmethod
    def _profile_values(
        first_name: str, last_name: str, school_nickname: str
    ) -> _ProfileValues:
        normalized_first_name = normalize_profile_text(first_name)
        normalized_last_name = normalize_profile_text(last_name)
        normalized_nickname = normalize_profile_text(school_nickname)
        if not normalized_first_name:
            raise MembershipError(MembershipErrorCode.FIRST_NAME_REQUIRED)
        if not normalized_last_name:
            raise MembershipError(MembershipErrorCode.LAST_NAME_REQUIRED)
        if not normalized_nickname:
            raise MembershipError(MembershipErrorCode.SCHOOL_NICKNAME_REQUIRED)
        display_name = f"{normalized_first_name} {normalized_last_name}"
        if any(
            len(value) > MAX_PROFILE_FIELD_LENGTH
            for value in (
                normalized_first_name,
                normalized_last_name,
                normalized_nickname,
                display_name,
            )
        ):
            raise MembershipError(MembershipErrorCode.PROFILE_FIELD_TOO_LONG)
        return _ProfileValues(
            first_name=normalized_first_name,
            last_name=normalized_last_name,
            school_nickname=normalized_nickname,
            display_name=display_name,
        )

    @staticmethod
    def _profile(player: Player) -> PlayerProfileView:
        return PlayerProfileView(
            id=player.id,
            club_id=player.club_id,
            first_name=player.first_name or "",
            last_name=player.last_name or "",
            school_nickname=player.school_nickname or "",
            display_name=player.display_name,
        )

    @staticmethod
    def _raise_profile_integrity_error(error: IntegrityError) -> None:
        original = error.orig
        cause = getattr(original, "__cause__", None)
        constraint_name = getattr(original, "constraint_name", None) or getattr(
            cause, "constraint_name", None
        )
        if constraint_name == "uq_players_club_school_nickname_ci":
            raise MembershipError(
                MembershipErrorCode.SCHOOL_NICKNAME_CONFLICT
            ) from error
        raise error

    @staticmethod
    def _validate_invite(invitation: InviteToken | None) -> None:
        if invitation is None or not invitation.is_active:
            raise MembershipError(MembershipErrorCode.INVITE_INVALID)
        if invitation.expires_at is not None and invitation.expires_at <= datetime.now(timezone.utc):
            raise MembershipError(MembershipErrorCode.INVITE_EXPIRED)
        if invitation.max_uses is not None and invitation.uses_count >= invitation.max_uses:
            raise MembershipError(MembershipErrorCode.INVITE_EXHAUSTED)
