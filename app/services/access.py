"""Resolve trusted Telegram identity into club-scoped actor facts."""

from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Club, ClubMembership, Player, TelegramUser
from app.domain.enums import MembershipStatus
from app.domain.permissions import ActorContext

__all__ = ["ActorContext", "resolve_actor", "resolve_actor_with_club"]


async def resolve_actor(
    session: AsyncSession, telegram_id: int, club_id: UUID
) -> ActorContext:
    """Return identity facts without making an authorization decision.

    The caller owns the session and its transaction. Missing users, memberships,
    and player profiles are represented by ``None`` fields in the context.
    """
    actor, _ = await resolve_actor_with_club(session, telegram_id, club_id)
    return actor


async def resolve_actor_with_club(
    session: AsyncSession, telegram_id: int, club_id: UUID
) -> tuple[ActorContext, Club | None]:
    """Resolve actor facts and return the club loaded by the same query."""
    row = (
        await session.execute(
            select(
                Club,
                TelegramUser.id.label("telegram_user_id"),
                ClubMembership.id.label("membership_id"),
                ClubMembership.role.label("membership_role"),
                ClubMembership.status.label("membership_status"),
                Player.id.label("player_id"),
            )
            .select_from(TelegramUser)
            .outerjoin(Club, Club.id == club_id)
            .outerjoin(
                ClubMembership,
                and_(
                    ClubMembership.telegram_user_id == TelegramUser.id,
                    ClubMembership.club_id == Club.id,
                ),
            )
            .outerjoin(
                Player,
                and_(
                    Player.telegram_user_id == TelegramUser.id,
                    Player.club_id == Club.id,
                ),
            )
            .where(TelegramUser.telegram_user_id == telegram_id)
        )
    ).one_or_none()

    if row is None:
        return (
            ActorContext(
                club_id=club_id,
                membership_role=None,
                membership_status=None,
                player_id=None,
                telegram_id=telegram_id,
            ),
            None,
        )

    return (
        ActorContext(
            club_id=club_id,
            membership_role=row.membership_role,
            membership_status=row.membership_status,
            player_id=row.player_id,
            # Membership status is the source of truth for registered members.
            # Player.is_active remains a synchronized legacy projection used by
            # historical-player queries in the existing bot.
            player_is_active=(
                row.player_id is not None
                and row.membership_status is MembershipStatus.ACTIVE
            ),
            telegram_id=telegram_id,
            telegram_user_id=row.telegram_user_id,
            membership_id=row.membership_id,
        ),
        row.Club,
    )
