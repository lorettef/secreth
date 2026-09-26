"""Session permissions, presence history, and creation concurrency on PostgreSQL."""

import asyncio
import os
import uuid
from unittest.mock import patch

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Club, ClubMembership, Game, GameSession, Player, SessionParticipant, TelegramUser
from app.domain.enums import GameStatus, MembershipRole, MembershipStatus, SessionStatus, Team
from app.services.sessions import SessionError, SessionErrorCode, SessionService

@pytest.fixture(scope="session")
def test_database_url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a dedicated PostgreSQL test database")
    parsed = make_url(url)
    if parsed.drivername != "postgresql+asyncpg" or not parsed.database.endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must point to a PostgreSQL database ending in _test")
    with patch.dict(os.environ, {"DATABASE_URL": url}):
        command.upgrade(Config("alembic.ini"), "head")
    return url


@pytest_asyncio.fixture
async def evening(test_database_url):
    engine = create_async_engine(test_database_url)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        factory = async_sessionmaker(bind=connection, expire_on_commit=False,
                                     join_transaction_mode="create_savepoint")
        async with factory() as db, db.begin():
            club = Club(name="Evening Club", timezone="Asia/Yakutsk")
            db.add(club)
            await db.flush()
            ids = []
            for telegram_id, role in ((7101, MembershipRole.PLAYER),
                                      (7102, MembershipRole.PLAYER),
                                      (7103, MembershipRole.ADMIN)):
                user = TelegramUser(telegram_user_id=telegram_id, first_name=str(telegram_id))
                db.add(user)
                await db.flush()
                player = Player(club_id=club.id, telegram_user_id=user.id,
                                display_name=str(telegram_id))
                db.add_all([player, ClubMembership(club_id=club.id, telegram_user_id=user.id,
                    role=role, status=MembershipStatus.ACTIVE)])
                await db.flush()
                ids.append(player.id)
            historical = Player(club_id=club.id, display_name="Historical")
            db.add(historical)
            await db.flush()
        yield SessionService(factory), factory, club.id, (*ids, historical.id)
        await transaction.rollback()
    await engine.dispose()


@pytest.mark.asyncio
async def test_any_player_starts_with_fewer_than_five_and_creator_manages(evening):
    service, _, club, players = evening
    assert (await service.menu_state(7101, club)).can_start
    view, created = await service.start(7101, club, (players[0], players[3]))
    assert created and view.can_manage
    assert view.game_count == view.liberal_wins == view.fascist_wins == 0
    assert {p.id for p in view.roster} == {players[0], players[3]}
    assert (await service.view(7102, view.id)).can_manage is False
    assert (await service.menu_state(7102, club)).active_session_id == view.id
    duplicate, created = await service.start(7102, club, ())
    assert not created and duplicate.id == view.id


@pytest.mark.asyncio
async def test_roster_leave_rejoin_preserves_intervals_and_permissions(evening):
    service, factory, club, players = evening
    view, _ = await service.start(7101, club, (players[0],))
    with pytest.raises(SessionError) as error:
        await service.add_player(7102, view.id, players[1])
    assert error.value.code is SessionErrorCode.NOT_STATISTICIAN
    view = await service.add_player(7101, view.id, players[3])
    assert len(view.roster) == 2
    view = await service.remove_player(7101, view.id, players[3])
    assert len(view.roster) == 1
    view = await service.add_player(7101, view.id, players[3])
    assert len(view.roster) == 2
    async with factory() as db:
        intervals = (await db.scalars(select(SessionParticipant).where(
            SessionParticipant.session_id == view.id,
            SessionParticipant.player_id == players[3]).order_by(SessionParticipant.joined_at))).all()
        assert len(intervals) == 2
        assert sum(p.is_present for p in intervals) == 1
        assert sum(p.left_at is not None for p in intervals) == 1
    with pytest.raises(SessionError) as error:
        await service.add_player(7101, view.id, players[3])
    assert error.value.code is SessionErrorCode.ALREADY_PRESENT


@pytest.mark.asyncio
async def test_transfer_old_stat_loses_rights_admin_keeps_them_and_finish(evening):
    service, factory, club, players = evening
    view, _ = await service.start(7101, club, (players[0], players[1]))
    choices = await service.roster_choices(7101, view.id, "transfer")
    assert {p.id for p in choices} == {players[1], players[2]}
    view = await service.transfer(7101, view.id, players[1])
    assert not view.can_manage
    with pytest.raises(SessionError) as error:
        await service.remove_player(7101, view.id, players[0])
    assert error.value.code is SessionErrorCode.NOT_STATISTICIAN
    assert (await service.view(7102, view.id)).can_manage
    assert (await service.view(7103, view.id)).can_manage
    await service.add_player(7103, view.id, players[3])
    finished = await service.finish(7103, view.id)
    assert finished.status is SessionStatus.FINISHED
    async with factory() as db:
        persisted = await db.get(GameSession, view.id)
        assert persisted.finished_at is not None
    for operation in (service.add_player(7102, view.id, players[0]),
                      service.remove_player(7103, view.id, players[1]),
                      service.transfer(7102, view.id, players[2]),
                      service.finish(7103, view.id)):
        with pytest.raises(SessionError) as error:
            await operation
        assert error.value.code is SessionErrorCode.SESSION_FINISHED
    next_evening, created = await service.start(7101, club, (players[0],))
    assert created and next_evening.id != view.id


@pytest.mark.asyncio
async def test_invalid_roster_and_transfer_are_rejected(evening):
    service, factory, club, players = evening
    with pytest.raises(SessionError) as error:
        await service.start(7101, club, (players[0], players[0]))
    assert error.value.code is SessionErrorCode.INVALID_SELECTION
    view, _ = await service.start(7101, club, ())
    with pytest.raises(SessionError) as error:
        await service.transfer(7101, view.id, players[3])
    assert error.value.code is SessionErrorCode.TRANSFER_NOT_AVAILABLE
    with pytest.raises(SessionError) as error:
        await service.remove_player(7101, view.id, players[0])
    assert error.value.code is SessionErrorCode.NOT_PRESENT


@pytest.mark.asyncio
async def test_evening_score_uses_only_non_void_games(evening):
    service, factory, club, players = evening
    view, _ = await service.start(7101, club, ())
    async with factory() as db, db.begin():
        db.add_all([
            Game(session_id=view.id, sequence_number=1, winner_team=Team.LIBERAL,
                 status=GameStatus.ACTIVE, created_by_player_id=players[0]),
            Game(session_id=view.id, sequence_number=2, winner_team=Team.FASCIST,
                 status=GameStatus.ACTIVE, created_by_player_id=players[0]),
            Game(session_id=view.id, sequence_number=3, winner_team=Team.FASCIST,
                 status=GameStatus.VOID, created_by_player_id=players[0]),
        ])
    updated = await service.view(7102, view.id)
    assert (updated.game_count, updated.liberal_wins, updated.fascist_wins) == (2, 1, 1)


@pytest.mark.asyncio
async def test_concurrent_start_creates_one_evening(test_database_url):
    engine = create_async_engine(test_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    club_id = uuid.uuid4()
    player_ids = [uuid.uuid4(), uuid.uuid4()]
    user_ids = [uuid.uuid4(), uuid.uuid4()]
    telegram_ids = [int(uuid.uuid4().int % 10_000_000_000) for _ in range(2)]
    try:
        async with factory() as db, db.begin():
            db.add(Club(id=club_id, name="Race Club", timezone="UTC"))
            for index in range(2):
                db.add(TelegramUser(id=user_ids[index], telegram_user_id=telegram_ids[index]))
                db.add(Player(id=player_ids[index], club_id=club_id,
                              telegram_user_id=user_ids[index], display_name=f"Race {index}"))
                db.add(ClubMembership(club_id=club_id, telegram_user_id=user_ids[index],
                                      role=MembershipRole.PLAYER, status=MembershipStatus.ACTIVE))
        service = SessionService(factory)
        results = await asyncio.gather(*(service.start(telegram_ids[index], club_id,
            (player_ids[index],)) for index in range(2)))
        assert [created for _, created in results].count(True) == 1
        assert results[0][0].id == results[1][0].id
        async with factory() as db:
            count = await db.scalar(select(func.count(GameSession.id)).where(
                GameSession.club_id == club_id, GameSession.status == SessionStatus.ACTIVE))
            assert count == 1
    finally:
        from sqlalchemy import delete
        async with factory() as db, db.begin():
            await db.execute(delete(SessionParticipant).where(SessionParticipant.session_id.in_(
                select(GameSession.id).where(GameSession.club_id == club_id))))
            await db.execute(delete(GameSession).where(GameSession.club_id == club_id))
            await db.execute(delete(ClubMembership).where(ClubMembership.club_id == club_id))
            await db.execute(delete(Player).where(Player.club_id == club_id))
            await db.execute(delete(TelegramUser).where(TelegramUser.id.in_(user_ids)))
            await db.execute(delete(Club).where(Club.id == club_id))
        await engine.dispose()
