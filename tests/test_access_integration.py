"""Club-scoped actor resolution against the PostgreSQL schema."""

import os
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.db.models import Club, ClubMembership, Player, TelegramUser
from app.domain.enums import MembershipRole, MembershipStatus
from app.services.access import resolve_actor


@pytest.fixture(scope="session")
def test_database_url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a dedicated PostgreSQL test database")
    parsed = make_url(url)
    if parsed.drivername != "postgresql+asyncpg" or not parsed.database.endswith(
        "_test"
    ):
        pytest.fail(
            "TEST_DATABASE_URL must point to a PostgreSQL database ending in _test"
        )
    with patch.dict(os.environ, {"DATABASE_URL": url}):
        command.upgrade(Config("alembic.ini"), "head")
    return url


@pytest_asyncio.fixture
async def access_db(test_database_url: str):
    engine = create_async_engine(test_database_url)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        async with AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        ) as session:
            yield session
        await transaction.rollback()
    await engine.dispose()


async def add_club(access_db: AsyncSession, name: str = "Access club") -> Club:
    club = Club(name=name, timezone="Asia/Yakutsk")
    access_db.add(club)
    await access_db.flush()
    return club


async def add_user(access_db: AsyncSession, telegram_id: int) -> TelegramUser:
    user = TelegramUser(telegram_user_id=telegram_id, first_name="Actor")
    access_db.add(user)
    await access_db.flush()
    return user


async def add_membership(
    access_db: AsyncSession,
    club: Club,
    user: TelegramUser,
    *,
    role: MembershipRole = MembershipRole.PLAYER,
    status: MembershipStatus = MembershipStatus.ACTIVE,
) -> ClubMembership:
    membership = ClubMembership(
        club_id=club.id,
        telegram_user_id=user.id,
        role=role,
        status=status,
    )
    access_db.add(membership)
    await access_db.flush()
    return membership


async def add_player(
    access_db: AsyncSession,
    club: Club,
    user: TelegramUser,
    *,
    active: bool = True,
) -> Player:
    player = Player(
        club_id=club.id,
        telegram_user_id=user.id,
        display_name="Actor",
        is_active=active,
    )
    access_db.add(player)
    await access_db.flush()
    return player


@pytest.mark.asyncio
async def test_active_member_with_linked_player(access_db: AsyncSession) -> None:
    club = await add_club(access_db)
    user = await add_user(access_db, 91001)
    membership = await add_membership(access_db, club, user, role=MembershipRole.ADMIN)
    player = await add_player(access_db, club, user)

    actor = await resolve_actor(access_db, 91001, club.id)

    assert actor.telegram_id == 91001
    assert actor.telegram_user_id == user.id
    assert actor.club_id == club.id
    assert actor.membership_id == membership.id
    assert actor.membership_role is MembershipRole.ADMIN
    assert actor.membership_status is MembershipStatus.ACTIVE
    assert actor.player_id == player.id
    assert actor.player_is_active


@pytest.mark.asyncio
async def test_owner_is_resolved_in_actor_context(access_db: AsyncSession) -> None:
    club = await add_club(access_db)
    user = await add_user(access_db, 91009)
    membership = await add_membership(access_db, club, user, role=MembershipRole.OWNER)
    player = await add_player(access_db, club, user)

    actor = await resolve_actor(access_db, 91009, club.id)

    assert actor.membership_id == membership.id
    assert actor.membership_role is MembershipRole.OWNER
    assert actor.membership_status is MembershipStatus.ACTIVE
    assert actor.player_id == player.id


@pytest.mark.asyncio
async def test_active_membership_without_player(access_db: AsyncSession) -> None:
    club = await add_club(access_db)
    user = await add_user(access_db, 91002)
    membership = await add_membership(access_db, club, user)

    actor = await resolve_actor(access_db, 91002, club.id)

    assert actor.telegram_user_id == user.id
    assert actor.membership_id == membership.id
    assert actor.membership_status is MembershipStatus.ACTIVE
    assert actor.player_id is None
    assert not actor.player_is_active


@pytest.mark.asyncio
async def test_membership_status_is_canonical_over_legacy_player_flag(
    access_db: AsyncSession,
) -> None:
    club = await add_club(access_db)
    user = await add_user(access_db, 91003)
    await add_membership(access_db, club, user)
    player = await add_player(access_db, club, user, active=False)

    actor = await resolve_actor(access_db, 91003, club.id)

    assert actor.membership_status is MembershipStatus.ACTIVE
    assert actor.player_id == player.id
    assert actor.player_is_active


@pytest.mark.asyncio
async def test_blocked_membership_is_preserved_in_context(
    access_db: AsyncSession,
) -> None:
    club = await add_club(access_db)
    user = await add_user(access_db, 91004)
    membership = await add_membership(
        access_db, club, user, status=MembershipStatus.BLOCKED
    )
    player = await add_player(access_db, club, user)

    actor = await resolve_actor(access_db, 91004, club.id)

    assert actor.membership_id == membership.id
    assert actor.membership_role is MembershipRole.PLAYER
    assert actor.membership_status is MembershipStatus.BLOCKED
    assert actor.player_id == player.id
    assert not actor.player_is_active


@pytest.mark.asyncio
async def test_missing_membership_preserves_telegram_user_identity(
    access_db: AsyncSession,
) -> None:
    club = await add_club(access_db)
    user = await add_user(access_db, 91005)

    actor = await resolve_actor(access_db, 91005, club.id)

    assert actor.telegram_user_id == user.id
    assert actor.membership_id is None
    assert actor.membership_role is None
    assert actor.membership_status is None
    assert actor.player_id is None


@pytest.mark.asyncio
async def test_membership_in_another_club_does_not_grant_target_access(
    access_db: AsyncSession,
) -> None:
    source = await add_club(access_db, "Source")
    target = await add_club(access_db, "Target")
    user = await add_user(access_db, 91006)
    await add_membership(access_db, source, user, role=MembershipRole.ADMIN)
    await add_player(access_db, source, user)

    actor = await resolve_actor(access_db, 91006, target.id)

    assert actor.telegram_user_id == user.id
    assert actor.club_id == target.id
    assert actor.membership_id is None
    assert actor.membership_role is None
    assert actor.membership_status is None
    assert actor.player_id is None


@pytest.mark.asyncio
async def test_player_from_another_club_is_not_bound_to_target_context(
    access_db: AsyncSession,
) -> None:
    target = await add_club(access_db, "Target")
    foreign = await add_club(access_db, "Foreign")
    user = await add_user(access_db, 91007)
    membership = await add_membership(access_db, target, user)
    foreign_player = await add_player(access_db, foreign, user)

    actor = await resolve_actor(access_db, 91007, target.id)

    assert actor.membership_id == membership.id
    assert actor.membership_status is MembershipStatus.ACTIVE
    assert actor.player_id is None
    assert actor.player_id != foreign_player.id
    assert not actor.player_is_active


@pytest.mark.asyncio
async def test_resolver_uses_passed_transaction_without_commit_or_rollback(
    access_db: AsyncSession,
) -> None:
    club = await add_club(access_db)
    user = await add_user(access_db, 91008)
    await add_membership(access_db, club, user)
    player = await add_player(access_db, club, user)
    commit = AsyncMock(side_effect=AssertionError("resolver must not commit"))
    rollback = AsyncMock(side_effect=AssertionError("resolver must not roll back"))

    with (
        patch.object(access_db, "commit", commit),
        patch.object(access_db, "rollback", rollback),
    ):
        actor = await resolve_actor(access_db, 91008, club.id)

    assert actor.player_id == player.id
    assert access_db.in_transaction()
    commit.assert_not_awaited()
    rollback.assert_not_awaited()
