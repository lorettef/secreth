"""Canonical membership statuses, access rules, hierarchy, and audit."""

import os
from dataclasses import dataclass
from unittest.mock import patch
from uuid import UUID

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import (
    AuditLog,
    Club,
    ClubMembership,
    Game,
    GamePlayer,
    GameSession,
    Player,
    TelegramUser,
)
from app.domain.enums import (
    GameRole,
    MembershipRole,
    MembershipStatus,
    SessionStatus,
    Team,
    VictoryType,
)
from app.services.access import resolve_actor
from app.services.games import GameError, GameErrorCode, GameService
from app.services.history import HistoryService
from app.services.membership import (
    MembershipError,
    MembershipErrorCode,
    MembershipService,
)
from app.services.sessions import SessionService
from app.services.stats import StatsError, StatsErrorCode, StatsService


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


@dataclass(frozen=True)
class StatusFixture:
    factory: async_sessionmaker[AsyncSession]
    memberships: MembershipService
    history: HistoryService
    stats: StatsService
    sessions: SessionService
    games: GameService
    club_id: UUID
    other_club_id: UUID
    owner_id: UUID
    admin_id: UUID
    member_id: UUID
    other_member_id: UUID
    session_id: UUID
    game_id: UUID


@pytest_asyncio.fixture
async def status_data(test_database_url: str):
    engine = create_async_engine(test_database_url)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        factory = async_sessionmaker(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        async with factory() as db, db.begin():
            club = Club(name="Status club", timezone="UTC")
            other_club = Club(name="Other club", timezone="UTC")
            db.add_all([club, other_club])
            await db.flush()

            records: dict[str, tuple[TelegramUser, Player]] = {}
            for key, telegram_id, target_club in (
                ("owner", 94001, club),
                ("admin", 94002, club),
                ("member", 94003, club),
                ("other_member", 94004, other_club),
            ):
                user = TelegramUser(telegram_user_id=telegram_id, first_name=key)
                db.add(user)
                await db.flush()
                player = Player(
                    club_id=target_club.id,
                    telegram_user_id=user.id,
                    display_name=key,
                )
                db.add(player)
                await db.flush()
                role = {
                    "owner": MembershipRole.OWNER,
                    "admin": MembershipRole.ADMIN,
                }.get(key, MembershipRole.PLAYER)
                db.add(
                    ClubMembership(
                        club_id=target_club.id,
                        telegram_user_id=user.id,
                        role=role,
                        status=MembershipStatus.ACTIVE,
                    )
                )
                records[key] = user, player

            evening = GameSession(
                club_id=club.id,
                status=SessionStatus.ACTIVE,
                created_by_player_id=records["member"][1].id,
                statistician_player_id=records["member"][1].id,
            )
            db.add(evening)
            await db.flush()
            game = Game(
                session_id=evening.id,
                sequence_number=1,
                winner_team=Team.LIBERAL,
                victory_type=VictoryType.LIBERAL_POLICIES,
                created_by_player_id=records["member"][1].id,
            )
            db.add(game)
            await db.flush()
            db.add(
                GamePlayer(
                    game_id=game.id,
                    player_id=records["member"][1].id,
                    role=GameRole.LIBERAL,
                )
            )
            await db.flush()

        yield StatusFixture(
            factory,
            MembershipService(factory),
            HistoryService(factory),
            StatsService(factory),
            SessionService(factory),
            GameService(factory),
            club.id,
            other_club.id,
            records["owner"][1].id,
            records["admin"][1].id,
            records["member"][1].id,
            records["other_member"][1].id,
            evening.id,
            game.id,
        )
        await transaction.rollback()
    await engine.dispose()


@pytest.mark.asyncio
async def test_active_member_can_read_and_participate(
    status_data: StatusFixture,
) -> None:
    data = status_data
    assert (
        await data.memberships.get_club(94003, data.club_id)
    ).status is MembershipStatus.ACTIVE
    assert (
        await data.sessions.menu_state(94003, data.club_id)
    ).active_session_id == data.session_id
    assert (await data.history.list_games(94003, data.club_id)).total == 1
    assert (await data.stats.get_my_stats(94003, data.club_id)).total_games == 1


@pytest.mark.asyncio
async def test_inactive_has_read_only_access_and_cannot_be_statistician(
    status_data: StatusFixture,
) -> None:
    data = status_data
    await data.memberships.change_member_status(
        94002, data.club_id, data.member_id, MembershipStatus.INACTIVE
    )

    assert (
        await data.memberships.get_club(94003, data.club_id)
    ).status is MembershipStatus.INACTIVE
    assert (await data.history.list_games(94003, data.club_id)).total == 1
    assert (await data.history.player(94003, data.admin_id))[1].id == data.admin_id
    assert (await data.stats.get_my_stats(94003, data.club_id)).total_games == 1
    assert not (await data.sessions.menu_state(94003, data.club_id)).can_start
    assert not (await data.sessions.view(94003, data.session_id)).can_manage
    with pytest.raises(GameError) as error:
        await data.games.setup(94003, data.session_id)
    assert error.value.code is GameErrorCode.NO_ACCESS


@pytest.mark.asyncio
async def test_blocked_member_and_blocked_admin_have_no_club_access(
    status_data: StatusFixture,
) -> None:
    data = status_data
    await data.memberships.change_member_status(
        94001, data.club_id, data.admin_id, MembershipStatus.BLOCKED
    )
    assert await data.memberships.list_clubs(94002) == ()
    with pytest.raises(MembershipError) as error:
        await data.memberships.get_admin_club(94002, data.club_id)
    assert error.value.code is MembershipErrorCode.BLOCKED
    with pytest.raises(StatsError) as error:
        await data.stats.get_my_stats(94002, data.club_id)
    assert error.value.code is StatsErrorCode.NO_ACCESS
    with pytest.raises(GameError) as error:
        await data.history.list_games(94002, data.club_id)
    assert error.value.code is GameErrorCode.NO_ACCESS
    async with data.factory() as db, db.begin():
        owner = await db.scalar(select(Player).where(Player.id == data.owner_id))
        owner.is_active = False
        owner_membership = await db.scalar(
            select(ClubMembership).where(
                ClubMembership.club_id == data.club_id,
                ClubMembership.telegram_user_id == owner.telegram_user_id,
            )
        )
        owner_membership.status = MembershipStatus.BLOCKED
    with pytest.raises(MembershipError) as error:
        await data.memberships.get_admin_club(94001, data.club_id)
    assert error.value.code is MembershipErrorCode.BLOCKED


@pytest.mark.asyncio
async def test_status_transitions_are_atomic_audited_and_idempotent(
    status_data: StatusFixture,
) -> None:
    data = status_data
    for status in (
        MembershipStatus.INACTIVE,
        MembershipStatus.ACTIVE,
        MembershipStatus.BLOCKED,
        MembershipStatus.ACTIVE,
    ):
        change = await data.memberships.change_member_status(
            94002, data.club_id, data.member_id, status
        )
        assert change.changed
        async with data.factory() as db:
            player = await db.get(Player, data.member_id)
            actor = await resolve_actor(db, 94003, data.club_id)
            assert actor.membership_status is status
            assert actor.player_is_active is (status is MembershipStatus.ACTIVE)
            assert player.is_active is (status is MembershipStatus.ACTIVE)

    repeated = await data.memberships.change_member_status(
        94002, data.club_id, data.member_id, MembershipStatus.ACTIVE
    )
    assert not repeated.changed
    async with data.factory() as db:
        logs = (
            await db.scalars(
                select(AuditLog)
                .where(
                    AuditLog.club_id == data.club_id,
                    AuditLog.action == "MEMBER_STATUS_CHANGE",
                )
                .order_by(AuditLog.created_at, AuditLog.id)
            )
        ).all()
        assert len(logs) == 4
        transitions = {
            (log.before_data["status"], log.after_data["status"]) for log in logs
        }
        assert transitions == {
            ("ACTIVE", "INACTIVE"),
            ("INACTIVE", "ACTIVE"),
            ("ACTIVE", "BLOCKED"),
            ("BLOCKED", "ACTIVE"),
        }


@pytest.mark.asyncio
async def test_admin_cannot_change_admin_or_owner_but_owner_can_change_admin(
    status_data: StatusFixture,
) -> None:
    data = status_data
    for target in (data.admin_id, data.owner_id):
        with pytest.raises(MembershipError) as error:
            await data.memberships.change_member_status(
                94002, data.club_id, target, MembershipStatus.INACTIVE
            )
        assert error.value.code is MembershipErrorCode.TARGET_ROLE_FORBIDDEN

    changed = await data.memberships.change_member_status(
        94001, data.club_id, data.admin_id, MembershipStatus.INACTIVE
    )
    assert changed.changed
    with pytest.raises(MembershipError) as error:
        await data.memberships.change_member_status(
            94001, data.club_id, data.owner_id, MembershipStatus.BLOCKED
        )
    assert error.value.code is MembershipErrorCode.TARGET_ROLE_FORBIDDEN


@pytest.mark.asyncio
async def test_status_change_is_club_scoped(status_data: StatusFixture) -> None:
    with pytest.raises(MembershipError) as error:
        await status_data.memberships.change_member_status(
            94001,
            status_data.club_id,
            status_data.other_member_id,
            MembershipStatus.INACTIVE,
        )
    assert error.value.code is MembershipErrorCode.TARGET_NOT_FOUND


@pytest.mark.asyncio
async def test_status_changes_preserve_games_and_derived_statistics(
    status_data: StatusFixture,
) -> None:
    data = status_data
    before = await data.stats.get_my_stats(94003, data.club_id)
    await data.memberships.change_member_status(
        94002, data.club_id, data.member_id, MembershipStatus.INACTIVE
    )
    after = await data.stats.get_my_stats(94003, data.club_id)
    async with data.factory() as db:
        assert (
            await db.scalar(select(func.count(Game.id)).where(Game.id == data.game_id))
            == 1
        )
        assert (
            await db.scalar(
                select(func.count(GamePlayer.id)).where(
                    GamePlayer.game_id == data.game_id
                )
            )
            == 1
        )
    assert (after.total_games, after.total_wins) == (
        before.total_games,
        before.total_wins,
    )
