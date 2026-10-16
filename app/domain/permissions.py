"""Club and session access decisions from trusted service context."""

from dataclasses import dataclass
from uuid import UUID

from app.domain.enums import MembershipRole, MembershipStatus, SessionStatus


@dataclass(frozen=True)
class ActorContext:
    club_id: UUID
    membership_role: MembershipRole | None
    membership_status: MembershipStatus | None
    player_id: UUID | None
    player_is_active: bool = False
    telegram_id: int | None = None
    telegram_user_id: UUID | None = None
    membership_id: UUID | None = None


@dataclass(frozen=True)
class SessionContext:
    club_id: UUID
    status: SessionStatus
    statistician_player_id: UUID


def _is_active_member(actor: ActorContext) -> bool:
    return (
        actor.membership_status is MembershipStatus.ACTIVE
        and actor.membership_role
        in (MembershipRole.PLAYER, MembershipRole.ADMIN, MembershipRole.OWNER)
    )


def can_view_club(actor: ActorContext, club_id: UUID) -> bool:
    """Allow read-only club access to active and inactive members."""
    return (
        actor.club_id == club_id
        and actor.membership_status
        in (MembershipStatus.ACTIVE, MembershipStatus.INACTIVE)
        and actor.membership_role
        in (MembershipRole.PLAYER, MembershipRole.ADMIN, MembershipRole.OWNER)
    )


def _has_admin_access(actor: ActorContext) -> bool:
    return _is_active_member(actor) and actor.membership_role in (
        MembershipRole.ADMIN,
        MembershipRole.OWNER,
    )


def _is_owner(actor: ActorContext) -> bool:
    return _is_active_member(actor) and actor.membership_role is MembershipRole.OWNER


def can_start_session(
    actor: ActorContext, club_id: UUID, *, has_active_session: bool
) -> bool:
    return (
        _is_active_member(actor)
        and actor.club_id == club_id
        and actor.player_id is not None
        and actor.player_is_active
        and not has_active_session
    )


def can_manage_current_session(actor: ActorContext, session: SessionContext) -> bool:
    if not _is_active_member(actor) or actor.club_id != session.club_id:
        return False
    if session.status is not SessionStatus.ACTIVE:
        return False
    return _has_admin_access(actor) or (
        actor.player_is_active and actor.player_id == session.statistician_player_id
    )


def can_create_game(actor: ActorContext, session: SessionContext) -> bool:
    return can_manage_current_session(actor, session)


def can_request_recovery(
    actor: ActorContext, session: SessionContext, *, is_present: bool
) -> bool:
    if not _is_active_member(actor) or actor.club_id != session.club_id:
        return False
    return session.status is SessionStatus.ACTIVE and (
        _has_admin_access(actor)
        or (actor.player_is_active and actor.player_id is not None and is_present)
    )


def can_confirm_recovery(
    actor: ActorContext,
    session: SessionContext,
    *,
    is_present: bool,
    initiator_player_id: UUID | None,
    candidate_player_id: UUID,
) -> bool:
    return (
        _is_active_member(actor)
        and actor.club_id == session.club_id
        and session.status is SessionStatus.ACTIVE
        and actor.player_is_active
        and actor.player_id is not None
        and is_present
        and actor.player_id not in (initiator_player_id, candidate_player_id)
    )


def can_edit_game(actor: ActorContext, session: SessionContext) -> bool:
    if not _is_active_member(actor) or actor.club_id != session.club_id:
        return False
    return _has_admin_access(actor) or can_manage_current_session(actor, session)


def can_manage_players(actor: ActorContext, club_id: UUID) -> bool:
    return actor.club_id == club_id and _has_admin_access(actor)


def can_manage_club(actor: ActorContext, club_id: UUID) -> bool:
    return actor.club_id == club_id and _has_admin_access(actor)


def can_manage_admins(actor: ActorContext, club_id: UUID) -> bool:
    return actor.club_id == club_id and _is_owner(actor)


def can_change_member_status(
    actor: ActorContext, club_id: UUID, target_role: MembershipRole
) -> bool:
    """Apply the role hierarchy for the ordinary member-status operation."""
    if actor.club_id != club_id or target_role is MembershipRole.OWNER:
        return False
    if not _has_admin_access(actor):
        return False
    return (
        actor.membership_role is MembershipRole.OWNER
        or target_role is MembershipRole.PLAYER
    )
