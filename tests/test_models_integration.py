"""PostgreSQL integration checks against the Alembic-managed test schema."""

import os
from unittest.mock import patch

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.db.models import (
    Club,
    ClubMembership,
    EventType,
    Game,
    GameEvent,
    GamePlayer,
    GameRole,
    GameSession,
    MembershipRole,
    MembershipStatus,
    Player,
    SessionStatus,
    Team,
    TelegramUser,
    VictoryType,
)


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
async def session(test_database_url: str):
    engine = create_async_engine(test_database_url)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        async with AsyncSession(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        ) as db_session:
            yield db_session
        await transaction.rollback()
    await engine.dispose()


async def seed_club_and_player(session: AsyncSession) -> tuple[Club, Player]:
    club = Club(name="Test club", timezone="Asia/Yakutsk")
    session.add(club)
    await session.flush()
    player = Player(club_id=club.id, display_name="Max")
    session.add(player)
    await session.flush()
    return club, player


def new_session(club: Club, player: Player, status: SessionStatus = SessionStatus.ACTIVE) -> GameSession:
    return GameSession(
        club_id=club.id,
        created_by_player_id=player.id,
        statistician_player_id=player.id,
        status=status,
    )


def new_game(game_session: GameSession, player: Player, sequence: int) -> Game:
    return Game(
        session_id=game_session.id,
        sequence_number=sequence,
        winner_team=Team.LIBERAL,
        victory_type=VictoryType.LIBERAL_POLICIES,
        created_by_player_id=player.id,
    )


@pytest.mark.asyncio
async def test_create_club_and_historical_player(session: AsyncSession) -> None:
    club, player = await seed_club_and_player(session)

    assert club.id is not None
    assert club.minimum_role_games_for_leaderboard == 5
    assert club.minimum_total_games_for_leaderboard == 10
    assert club.created_at.tzinfo is not None
    assert player.club_id == club.id
    assert player.telegram_user_id is None
    assert player.first_name is None
    assert player.last_name is None
    assert player.school_nickname is None


@pytest.mark.asyncio
async def test_school_nickname_unique_index_is_case_insensitive_per_club(
    session: AsyncSession,
) -> None:
    club, player = await seed_club_and_player(session)
    player.school_nickname = "SchoolNick"
    await session.flush()

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(
                Player(
                    club_id=club.id,
                    display_name="Duplicate nickname",
                    school_nickname="schoolnick",
                )
            )
            await session.flush()

    another_club = Club(name="Nickname in another club", timezone="UTC")
    session.add(another_club)
    await session.flush()
    session.add(
        Player(
            club_id=another_club.id,
            display_name="Allowed nickname",
            school_nickname="SCHOOLNICK",
        )
    )
    await session.flush()


@pytest.mark.asyncio
async def test_telegram_link_is_unique_within_club(session: AsyncSession) -> None:
    club, player = await seed_club_and_player(session)
    telegram = TelegramUser(telegram_user_id=123456, first_name="Max")
    session.add(telegram)
    await session.flush()
    player.telegram_user_id = telegram.id
    await session.flush()

    another_player = Player(club_id=club.id, display_name="Duplicate", telegram_user_id=telegram.id)
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(another_player)
            await session.flush()

    another_club = Club(name="Other club", timezone="UTC")
    session.add(another_club)
    await session.flush()
    session.add(Player(club_id=another_club.id, display_name="Max", telegram_user_id=telegram.id))
    await session.flush()

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(TelegramUser(telegram_user_id=123456))
            await session.flush()


@pytest.mark.asyncio
async def test_club_cannot_have_two_owner_memberships(session: AsyncSession) -> None:
    club = Club(name="Owner club", timezone="UTC")
    first_user = TelegramUser(telegram_user_id=123457)
    second_user = TelegramUser(telegram_user_id=123458)
    session.add_all((club, first_user, second_user))
    await session.flush()
    session.add(
        ClubMembership(
            club_id=club.id,
            telegram_user_id=first_user.id,
            role=MembershipRole.OWNER,
            status=MembershipStatus.ACTIVE,
        )
    )
    await session.flush()

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(
                ClubMembership(
                    club_id=club.id,
                    telegram_user_id=second_user.id,
                    role=MembershipRole.OWNER,
                    status=MembershipStatus.ACTIVE,
                )
            )
            await session.flush()


@pytest.mark.asyncio
async def test_different_clubs_can_each_have_an_owner(session: AsyncSession) -> None:
    first_club = Club(name="First owner club", timezone="UTC")
    second_club = Club(name="Second owner club", timezone="UTC")
    first_user = TelegramUser(telegram_user_id=123459)
    second_user = TelegramUser(telegram_user_id=123460)
    session.add_all((first_club, second_club, first_user, second_user))
    await session.flush()
    session.add_all(
        (
            ClubMembership(
                club_id=first_club.id,
                telegram_user_id=first_user.id,
                role=MembershipRole.OWNER,
                status=MembershipStatus.ACTIVE,
            ),
            ClubMembership(
                club_id=second_club.id,
                telegram_user_id=second_user.id,
                role=MembershipRole.OWNER,
                status=MembershipStatus.ACTIVE,
            ),
        )
    )
    await session.flush()


@pytest.mark.asyncio
async def test_one_active_session_per_club(session: AsyncSession) -> None:
    club, player = await seed_club_and_player(session)
    first = new_session(club, player)
    session.add(first)
    await session.flush()
    assert first.started_at.tzinfo is not None

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(new_session(club, player))
            await session.flush()

    session.add(new_session(club, player, SessionStatus.FINISHED))
    await session.flush()


@pytest.mark.asyncio
async def test_game_sequence_unique_within_session(session: AsyncSession) -> None:
    club, player = await seed_club_and_player(session)
    game_session = new_session(club, player)
    session.add(game_session)
    await session.flush()
    session.add(new_game(game_session, player, 1))
    await session.flush()

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(new_game(game_session, player, 1))
            await session.flush()

    session.add(new_game(game_session, player, 2))
    await session.flush()


@pytest.mark.asyncio
async def test_game_player_unique_within_game(session: AsyncSession) -> None:
    club, player = await seed_club_and_player(session)
    game_session = new_session(club, player)
    session.add(game_session)
    await session.flush()
    game = new_game(game_session, player, 1)
    session.add(game)
    await session.flush()
    session.add(GamePlayer(game_id=game.id, player_id=player.id, role=GameRole.LIBERAL))
    await session.flush()

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(GamePlayer(game_id=game.id, player_id=player.id, role=GameRole.HITLER))
            await session.flush()


@pytest.mark.asyncio
async def test_event_player_must_be_game_participant(session: AsyncSession) -> None:
    club, player = await seed_club_and_player(session)
    outsider = Player(club_id=club.id, display_name="Outsider")
    session.add(outsider)
    game_session = new_session(club, player)
    session.add(game_session)
    await session.flush()
    game = new_game(game_session, player, 1)
    session.add(game)
    await session.flush()
    session.add(GamePlayer(game_id=game.id, player_id=player.id, role=GameRole.LIBERAL))
    await session.flush()

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(
                GameEvent(
                    game_id=game.id,
                    player_id=outsider.id,
                    event_type=EventType.PALIVO,
                    created_by_player_id=player.id,
                )
            )
            await session.flush()
