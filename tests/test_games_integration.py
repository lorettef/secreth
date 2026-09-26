"""Completed game writes against the migrated PostgreSQL schema."""

import asyncio
import os
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock
from unittest.mock import patch

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message, User

from app.db.models import Club, ClubMembership, Game, GamePlayer, GameSession, Player, SessionParticipant, TelegramUser
from app.domain.enums import GameRole, MembershipRole, MembershipStatus, SessionStatus, Team, VictoryType
from app.services.games import GameError, GameErrorCode, GameService
from app.services.sessions import SessionService
from app.bot.callbacks import GameAction, GameChoice, GamePick, GameSave
from app.bot.handlers.games import begin_wizard, pick, action, choice, save as save_callback
from app.locales import ru


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


async def seed(factory, *, telegram_ids: tuple[int, int] = (8801, 8802)):
    async with factory() as db, db.begin():
        club = Club(name="Game Club", timezone="UTC")
        db.add(club)
        await db.flush()
        players = []
        users = []
        for index in range(10):
            user = TelegramUser(telegram_user_id=telegram_ids[index]) if index < 2 else None
            if user:
                db.add(user)
                await db.flush()
                users.append(user.id)
                db.add(ClubMembership(club_id=club.id, telegram_user_id=user.id,
                    role=MembershipRole.PLAYER if index == 0 else MembershipRole.ADMIN,
                    status=MembershipStatus.ACTIVE))
            player = Player(club_id=club.id, display_name=f"Player {index:02d}",
                            telegram_user_id=user.id if user else None)
            db.add(player)
            await db.flush()
            players.append(player.id)
        evening = GameSession(club_id=club.id, created_by_player_id=players[0],
                              statistician_player_id=players[0])
        db.add(evening)
        await db.flush()
        db.add_all(SessionParticipant(session_id=evening.id, player_id=pid) for pid in players)
        await db.flush()
        return club.id, evening.id, tuple(players), tuple(users)


@pytest_asyncio.fixture
async def games(test_database_url):
    engine = create_async_engine(test_database_url)
    async with engine.connect() as connection:
        tx = await connection.begin()
        factory = async_sessionmaker(bind=connection, expire_on_commit=False,
                                     join_transaction_mode="create_savepoint")
        club, evening, players, _ = await seed(factory)
        yield GameService(factory), SessionService(factory), factory, club, evening, players
        await tx.rollback()
    await engine.dispose()


def save(service, evening, players, *, telegram_id=8801, draft=None,
         winner=Team.LIBERAL, victory=VictoryType.HITLER_KILLED):
    participants = tuple(players)
    fascist_count = 1 if len(participants) in (5, 6) else 2 if len(participants) in (7, 8) else 3
    return service.create_game(telegram_id, evening, draft or uuid.uuid4(), participants,
                               participants[0], participants[1:1 + fascist_count], winner, victory)


@pytest.mark.asyncio
@pytest.mark.parametrize("count,fascists,liberals", [(5, 1, 3), (6, 1, 4), (7, 2, 4),
                                                     (8, 2, 5), (9, 3, 5), (10, 3, 6)])
async def test_game_roles_for_all_supported_sizes(games, count, fascists, liberals):
    service, _, factory, _, evening, players = games
    result, created = await save(service, evening, players[:count])
    assert created and result.sequence_number == 1
    assert len(result.participants) == count
    assert len(result.fascists) == fascists
    assert len(result.liberals) == liberals
    assert result.hitler.id not in {p.id for p in result.fascists}
    async with factory() as db:
        roles = (await db.scalars(select(GamePlayer.role).where(GamePlayer.game_id == result.id))).all()
        assert roles.count(GameRole.HITLER) == 1
        assert roles.count(GameRole.FASCIST) == fascists
        assert roles.count(GameRole.LIBERAL) == liberals


@pytest.mark.asyncio
async def test_setup_blocks_short_roster_and_defaults_to_current_roster(games):
    service, _, factory, _, evening, players = games
    setup = await service.setup(8801, evening)
    assert len(setup.roster) == 10 and setup.sequence_number == 1
    async with factory() as db, db.begin():
        rows = (await db.scalars(select(SessionParticipant).where(
            SessionParticipant.session_id == evening, SessionParticipant.player_id.in_(players[4:])))).all()
        for row in rows:
            row.is_present = False
            from datetime import datetime, timezone
            row.left_at = datetime.now(timezone.utc)
    with pytest.raises(GameError) as error:
        await service.setup(8801, evening)
    assert error.value.code is GameErrorCode.TOO_FEW_PRESENT
    with pytest.raises(GameError) as error:
        await save(service, evening, players[:5])
    assert error.value.code is GameErrorCode.TOO_FEW_PRESENT


@pytest.mark.asyncio
async def test_duplicate_players_roles_and_victory_rejected(games):
    service, _, factory, _, evening, players = games
    cases = [
        (players[:4] + (players[0],), players[0], (players[1],), Team.LIBERAL,
         VictoryType.HITLER_KILLED, GameErrorCode.INVALID_PARTICIPANTS),
        (players[:5], players[0], (players[0],), Team.LIBERAL,
         VictoryType.HITLER_KILLED, GameErrorCode.INVALID_ROLES),
        (players[:5], players[0], (players[1], players[2]), Team.LIBERAL,
         VictoryType.HITLER_KILLED, GameErrorCode.INVALID_ROLES),
        (players[:5], players[0], (players[1],), Team.FASCIST,
         VictoryType.HITLER_KILLED, GameErrorCode.INVALID_VICTORY),
        (players[:5], players[0], (players[1],), Team.LIBERAL,
         VictoryType.UNKNOWN, GameErrorCode.INVALID_VICTORY),
    ]
    for participants, hitler, fascists, winner, victory, expected in cases:
        with pytest.raises(GameError) as error:
            await service.create_game(8801, evening, uuid.uuid4(), participants,
                                      hitler, fascists, winner, victory)
        assert error.value.code is expected
    with pytest.raises(GameError) as error:
        await service.create_game(8801, evening, None, players[:5], players[0],
                                  (players[1],), Team.LIBERAL, VictoryType.HITLER_KILLED)
    assert error.value.code is GameErrorCode.DRAFT_CONFLICT
    async with factory() as db:
        assert await db.scalar(select(func.count(Game.id))) == 0


@pytest.mark.asyncio
async def test_permissions_finished_and_cross_club(games):
    service, _, factory, _, evening, players = games
    with pytest.raises(GameError) as error:
        await save(service, evening, players[:5], telegram_id=9999)
    assert error.value.code is GameErrorCode.NO_ACCESS
    async with factory() as db, db.begin():
        user = TelegramUser(telegram_user_id=8803, first_name="Observer")
        db.add(user)
        await db.flush()
        db.add_all([Player(club_id=(await db.get(GameSession, evening)).club_id,
                           telegram_user_id=user.id, display_name="Observer"),
                    ClubMembership(club_id=(await db.get(GameSession, evening)).club_id,
                                   telegram_user_id=user.id, role=MembershipRole.PLAYER,
                                   status=MembershipStatus.ACTIVE)])
    with pytest.raises(GameError) as error:
        await save(service, evening, players[:5], telegram_id=8803)
    assert error.value.code is GameErrorCode.NOT_STATISTICIAN
    other_club = Club(name="Other", timezone="UTC")
    async with factory() as db, db.begin():
        db.add(other_club)
        await db.flush()
        outsider = Player(club_id=other_club.id, display_name="Outsider")
        db.add(outsider)
        await db.flush()
        outsider_id = outsider.id
    with pytest.raises(GameError) as error:
        await service.create_game(8801, evening, uuid.uuid4(), players[:4] + (outsider_id,),
                                  players[0], (players[1],), Team.LIBERAL,
                                  VictoryType.HITLER_KILLED)
    assert error.value.code is GameErrorCode.INVALID_PARTICIPANTS
    async with factory() as db, db.begin():
        session = await db.get(GameSession, evening)
        session.status = SessionStatus.FINISHED
    with pytest.raises(GameError) as error:
        await save(service, evening, players[:5])
    assert error.value.code is GameErrorCode.SESSION_FINISHED


@pytest.mark.asyncio
async def test_double_save_and_roster_snapshot(games):
    service, sessions, factory, _, evening, players = games
    draft = uuid.uuid4()
    first, created = await save(service, evening, players[:8], draft=draft)
    second, created_again = await save(service, evening, players[:8], draft=draft)
    assert created and not created_again and first.id == second.id
    await sessions.remove_player(8801, evening, players[1])
    await sessions.add_player(8801, evening, players[1])
    await sessions.remove_player(8801, evening, players[2])
    saved = await service.game(8802, first.id)
    assert {p.id for p in saved.participants} == set(players[:8])
    assert saved.hitler.id == players[0]
    assert (await sessions.view(8801, evening)).game_count == 1
    async with factory() as db:
        assert await db.scalar(select(func.count(Game.id))) == 1


@pytest.mark.asyncio
async def test_concurrent_saves_allocate_distinct_sequence_numbers(test_database_url):
    engine = create_async_engine(test_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    telegram_ids = (int(uuid.uuid4().int % 10_000_000_000), int(uuid.uuid4().int % 10_000_000_000))
    club = evening = None
    try:
        club, evening, players, users = await seed(factory, telegram_ids=telegram_ids)
        service = GameService(factory)
        results = await asyncio.gather(*(save(service, evening, players[:8], telegram_id=tid)
                                         for tid in telegram_ids))
        assert sorted(game.sequence_number for game, _ in results) == [1, 2]
        assert all(created for _, created in results)
        draft = uuid.uuid4()
        duplicate_results = await asyncio.gather(*(save(service, evening, players[:8],
            telegram_id=telegram_ids[0], draft=draft) for _ in range(2)))
        assert {game.id for game, _ in duplicate_results} == {duplicate_results[0][0].id}
        assert sorted(created for _, created in duplicate_results) == [False, True]
        async with factory() as db:
            assert await db.scalar(select(func.count(Game.id)).where(Game.session_id == evening)) == 3
    finally:
        if club is not None:
            async with factory() as db, db.begin():
                await db.execute(delete(GamePlayer).where(GamePlayer.game_id.in_(
                    select(Game.id).where(Game.session_id == evening))))
                await db.execute(delete(Game).where(Game.session_id == evening))
                await db.execute(delete(SessionParticipant).where(SessionParticipant.session_id == evening))
                await db.execute(delete(GameSession).where(GameSession.id == evening))
                await db.execute(delete(ClubMembership).where(ClubMembership.club_id == club))
                await db.execute(delete(Player).where(Player.club_id == club))
                await db.execute(delete(TelegramUser).where(TelegramUser.id.in_(users)))
                await db.execute(delete(Club).where(Club.id == club))
        await engine.dispose()


@pytest.mark.asyncio
async def test_eight_player_telegram_wizard_saves_only_on_final_tap(games, monkeypatch):
    service, _, factory, _, evening, players = games
    storage = MemoryStorage()
    state = FSMContext(storage, StorageKey(bot_id=1, chat_id=8801, user_id=8801))
    answer = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer)
    monkeypatch.setattr(CallbackQuery, "answer", AsyncMock())
    message = Message(message_id=1, date=datetime.now(timezone.utc),
                      chat=Chat(id=8801, type="private"),
                      from_user=User(id=8801, is_bot=False, first_name="Statistician"))
    callback = CallbackQuery(id="test", from_user=message.from_user,
                             chat_instance="test", message=message, data="unused")
    async with factory() as db, db.begin():
        for pid in players[8:]:
            interval = await db.scalar(select(SessionParticipant).where(
                SessionParticipant.session_id == evening, SessionParticipant.player_id == pid))
            interval.is_present = False
            interval.left_at = datetime.now(timezone.utc)
    await begin_wizard(callback, state, service, evening)
    assert (await state.get_data())["stage"] == "hitler"
    assert "Кто был Hitler" in answer.await_args.args[0]
    await pick(callback, GamePick(step="hitler", player_id=players[0]), state)
    assert "0/2" in answer.await_args.args[0]
    await pick(callback, GamePick(step="fascists", player_id=players[1]), state)
    await pick(callback, GamePick(step="fascists", player_id=players[2]), state)
    await action(callback, GameAction(action="fascists_done"), state)
    await choice(callback, GameChoice(kind="winner", code="L"), state)
    await choice(callback, GameChoice(kind="victory", code="HK"), state)
    assert "Liberals:" in answer.await_args.args[0]
    assert "Сохранить" in str(answer.await_args.kwargs["reply_markup"])
    async with factory() as db:
        assert await db.scalar(select(func.count(Game.id))) == 0
    draft = uuid.UUID((await state.get_data())["draft_id"])
    await save_callback(callback, GameSave(draft_id=draft), state, service)
    assert "Игра #1 сохранена" in answer.await_args.args[0]
    await save_callback(callback, GameSave(draft_id=draft), state, service)
    async with factory() as db:
        assert await db.scalar(select(func.count(Game.id))) == 1
    await begin_wizard(callback, state, service, evening)
    new_draft = (await state.get_data())["draft_id"]
    await save_callback(callback, GameSave(draft_id=draft), state, service)
    assert (await state.get_data())["draft_id"] == new_draft
    await storage.close()


@pytest.mark.asyncio
async def test_more_than_ten_present_forces_participant_selection(games, monkeypatch):
    service, _, factory, club, evening, _ = games
    async with factory() as db, db.begin():
        extra = Player(club_id=club, display_name="Player 11")
        away = Player(club_id=club, display_name="Away")
        db.add_all([extra, away])
        await db.flush()
        db.add(SessionParticipant(session_id=evening, player_id=extra.id))
    storage = MemoryStorage()
    state = FSMContext(storage, StorageKey(bot_id=1, chat_id=8801, user_id=8801))
    answer = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer)
    monkeypatch.setattr(CallbackQuery, "answer", AsyncMock())
    message = Message(message_id=1, date=datetime.now(timezone.utc),
                      chat=Chat(id=8801, type="private"),
                      from_user=User(id=8801, is_bot=False, first_name="Statistician"))
    callback = CallbackQuery(id="test", from_user=message.from_user,
                             chat_instance="test", message=message, data="unused")
    await begin_wizard(callback, state, service, evening)
    assert away.id not in {p.id for p in (await service.setup(8801, evening)).candidates}
    assert (await state.get_data())["stage"] == "participants"
    assert (await state.get_data())["participants"] == []
    await action(callback, GameAction(action="participants_done"), state)
    assert (await state.get_data())["stage"] == "participants"
    assert answer.await_args.args[0] == ru.GAME_ERRORS[GameErrorCode.INVALID_PARTICIPANTS.value]
    for pid in (await service.setup(8801, evening)).candidates[:5]:
        await pick(callback, GamePick(step="participants", player_id=pid.id), state)
    await action(callback, GameAction(action="participants_done"), state)
    assert (await state.get_data())["stage"] == "hitler"
    assert len((await state.get_data())["participants"]) == 5
    await storage.close()
