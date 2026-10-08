"""History, corrections, audit and derived statistics on real migrated PostgreSQL."""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import event, func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import AuditLog, ClubMembership, Game, GameEvent, GamePlayer, GameSession, Player
from app.domain.enums import EventType, GameRole, GameStatus, SessionStatus, Team, VictoryType
from app.services.events import EventError, EventService
from app.services.games import GameError, GameService
from app.services.history import HistoryService
from app.services.sessions import SessionError, SessionService
from app.services.stats import StatsService
from app.locales import ru
from test_games_integration import seed, test_database_url  # noqa: F401


@dataclass
class Fixture:
    factory: object
    engine: object
    history: HistoryService
    games: GameService
    events: EventService
    stats: StatsService
    sessions: SessionService
    club: UUID
    evening: UUID
    players: tuple[UUID, ...]
    game: UUID
    other_club: UUID
    other_evening: UUID
    others: tuple[UUID, ...]
    other_game: UUID


@pytest_asyncio.fixture
async def history_data(test_database_url):
    engine = create_async_engine(test_database_url)
    async with engine.connect() as connection:
        tx = await connection.begin()
        factory = async_sessionmaker(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
        club, evening, players, _ = await seed(factory)
        other, other_evening, others, _ = await seed(factory, telegram_ids=(8901, 8902))
        games = GameService(factory)
        game, _ = await games.create_game(8801, evening, uuid4(), players[:5], players[0], (players[1],),
                                           Team.FASCIST, VictoryType.FASCIST_POLICIES)
        other_game, _ = await games.create_game(8901, other_evening, uuid4(), others[:5], others[0], (others[1],),
                                               Team.LIBERAL, VictoryType.HITLER_KILLED)
        yield Fixture(factory, engine, HistoryService(factory), games, EventService(factory), StatsService(factory),
                      SessionService(factory), club, evening, players, game.id, other, other_evening, others, other_game.id)
        await tx.rollback()
    await engine.dispose()


async def edit(f, *, actor=8802, game=None, participants=None, hitler=None, fascists=None,
               winner=Team.LIBERAL, victory=VictoryType.LIBERAL_POLICIES, revision=None):
    gid = game or f.game
    card = await f.history.card(8802 if gid == f.game else 8902, gid)
    return await f.games.edit_game(actor, gid, expected_revision=revision or card.revision,
        participants=participants if participants is not None else tuple(p.id for p in card.participants),
        hitler=hitler if hitler is not None else next(pid for pid, role in card.roles.items() if role is GameRole.HITLER),
        fascists=fascists if fascists is not None else tuple(pid for pid, role in card.roles.items() if role is GameRole.FASCIST),
        winner=winner, victory_type=victory)


async def void(f, *, actor=8802, game=None, restore=False, revision=None):
    gid = game or f.game
    card = await f.history.card(8802 if gid == f.game else 8902, gid)
    await f.games.set_game_void(actor, gid, expected_revision=revision or card.revision, restore=restore)


async def moment(f, player=None):
    return (await f.events.create_event(8801, f.game, uuid4(), player or f.players[1], EventType.IQ_200, comment="original"))[0]


async def audit_rows(f):
    async with f.factory() as db:
        return (await db.scalars(select(AuditLog).where(AuditLog.club_id == f.club).order_by(AuditLog.created_at, AuditLog.id))).all()


@pytest.mark.asyncio
async def test_sessions_recent_player_games_and_cards_use_snapshots(history_data):
    f = history_data
    s = await f.history.list_sessions(8801, f.club)
    assert s.total == 1 and s.items[0].games_count == 1
    games = await f.history.list_games(8801, f.club, session_id=f.evening)
    assert games.total == 1 and games.items[0].player_count == 5
    assert (await f.history.list_games(8801, f.club, player_id=f.players[5])).total == 0
    assert (await f.history.list_games(8801, f.club, player_id=f.players[0])).total == 1
    e = await moment(f)
    card = await f.history.card(8801, f.game)
    assert card.roles == {f.players[0]: GameRole.HITLER, f.players[1]: GameRole.FASCIST,
                          **{p: GameRole.LIBERAL for p in f.players[2:5]}}
    assert card.events.items == (e,) and card.can_edit and not card.is_admin
    admin = await f.history.card(8802, f.game)
    assert admin.is_admin and admin.can_edit
    async with f.factory() as db, db.begin():
        (await db.get(GameSession, f.evening)).statistician_player_id = f.players[2]
    ordinary = await f.history.card(8801, f.game)
    assert not ordinary.can_edit and not ordinary.can_add_event


@pytest.mark.asyncio
async def test_admin_finished_correction_void_restore_end_to_end_audit_order(history_data):
    f = history_data
    await moment(f)
    async with f.factory() as db, db.begin():
        (await db.get(GameSession, f.evening)).status = SessionStatus.FINISHED
    assert (await f.history.list_sessions(8802, f.club)).items[0].status is SessionStatus.FINISHED
    assert (await f.history.list_games(8802, f.club)).items[0].id == f.game
    assert (await f.history.card(8802, f.game)).can_edit
    assert (await f.stats.get_my_stats(8801, f.club)).total_wins == 1
    assert (await f.stats.get_club_stats(8801, f.club)).score.fascist_wins == 1
    await edit(f)
    corrected = await f.stats.get_my_stats(8801, f.club)
    assert corrected.total_games == 1 and corrected.total_wins == 0
    club = await f.stats.get_club_stats(8801, f.club)
    assert club.score.liberal_wins == 1 and club.score.fascist_wins == 0
    await void(f)
    assert (await f.stats.get_my_stats(8801, f.club)).total_games == 0
    assert (await f.stats.get_session_stats(8801, f.club, f.evening)).score.games_count == 0
    assert all(v == 0 for v in (await f.stats.get_club_stats(8801, f.club)).event_counts.values())
    await void(f, restore=True)
    assert await f.stats.get_my_stats(8801, f.club) == corrected
    logs = await audit_rows(f)
    assert [row.action for row in logs] == ["GAME_EDIT", "GAME_VOID", "GAME_RESTORE"]
    assert logs[0].before_data["winner"] == "FASCIST" and logs[0].after_data["winner"] == "LIBERAL"
    assert len(logs[0].before_data["participants"]) == 5
    assert logs[1].before_data["status"] == "ACTIVE" and logs[1].after_data["status"] == "VOID"
    assert logs[2].before_data["status"] == "VOID" and logs[2].after_data["status"] == "ACTIVE"
    assert all(row.actor_player_id == f.players[1] and row.entity_id == f.game for row in logs)


@pytest.mark.asyncio
async def test_statistician_can_edit_only_current_active_session(history_data):
    f = history_data
    await edit(f, actor=8801)
    assert (await audit_rows(f))[-1].action == "GAME_EDIT"
    async with f.factory() as db, db.begin():
        (await db.get(GameSession, f.evening)).status = SessionStatus.FINISHED
    with pytest.raises(GameError, match="NO_ACCESS"):
        await edit(f, actor=8801)
    await edit(f, actor=8802)


@pytest.mark.asyncio
async def test_ordinary_player_and_former_statistician_cannot_edit(history_data):
    f = history_data
    async with f.factory() as db, db.begin():
        (await db.get(GameSession, f.evening)).statistician_player_id = f.players[2]
    with pytest.raises(GameError, match="NO_ACCESS"):
        await edit(f, actor=8801)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["count", "duplicate", "fascist_count", "hitler_missing", "hitler_fascist", "wrong_victory", "club"])
async def test_invalid_edit_is_transactional_and_creates_no_audit(history_data, invalid):
    f = history_data
    before = await f.history.card(8802, f.game)
    kwargs = {
        "count": {"participants": f.players[:4]},
        "duplicate": {"participants": f.players[:4] + (f.players[0],)},
        "fascist_count": {"fascists": (f.players[1], f.players[2])},
        "hitler_missing": {"hitler": f.players[7]},
        "hitler_fascist": {"hitler": f.players[1]},
        "wrong_victory": {"winner": Team.LIBERAL, "victory": VictoryType.HITLER_CHANCELLOR},
        "club": {"participants": (f.others[0],) + f.players[1:5], "hitler": f.others[0]},
    }[invalid]
    with pytest.raises(GameError):
        await edit(f, **kwargs)
    assert await f.history.card(8802, f.game) == before
    assert not await audit_rows(f)


@pytest.mark.asyncio
async def test_role_correction_preserves_events_and_recalculates_role_stats(history_data):
    f = history_data
    e = await moment(f)
    before = await f.stats.get_player_stats(8801, f.club, f.players[1])
    await edit(f, fascists=(f.players[2],), winner=Team.FASCIST, victory=VictoryType.FASCIST_POLICIES)
    after = await f.stats.get_player_stats(8801, f.club, f.players[1])
    assert after.total_games == before.total_games == 1
    assert before.roles[GameRole.FASCIST].games == 1 and after.roles[GameRole.FASCIST].games == 0
    assert after.roles[GameRole.LIBERAL].games == 1
    assert await f.events.event(8801, e.id) == e
    assert (await f.stats.get_session_stats(8801, f.club, f.evening)).score.fascist_wins == 1


@pytest.mark.asyncio
async def test_participant_replacement_leaves_unrelated_games_unchanged(history_data):
    f = history_data
    second, _ = await f.games.create_game(8801, f.evening, uuid4(), f.players[:5], f.players[0], (f.players[1],),
                                          Team.LIBERAL, VictoryType.HITLER_KILLED)
    before = await f.history.card(8802, second.id)
    await edit(f, participants=f.players[:4] + (f.players[5],))
    assert (await f.history.list_games(8801, f.club, player_id=f.players[4])).total == 1
    assert (await f.history.list_games(8801, f.club, player_id=f.players[5])).total == 1
    assert (await f.stats.get_player_stats(8801, f.club, f.players[4])).total_games == 1
    assert (await f.stats.get_player_stats(8801, f.club, f.players[5])).total_games == 1
    assert await f.history.card(8802, second.id) == before
    async with f.factory() as db:
        assert await db.scalar(select(GamePlayer.id).where(GamePlayer.game_id == f.game, GamePlayer.player_id == f.players[4])) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("resolution", ["delete", "reassign"])
async def test_participant_with_event_cannot_be_removed_until_conflict_resolved(history_data, resolution):
    f = history_data
    e = await moment(f, f.players[4])
    before = await f.history.card(8802, f.game)
    with pytest.raises(GameError, match="EVENT_CONFLICT"):
        await edit(f, participants=f.players[:4] + (f.players[5],))
    assert await f.history.card(8802, f.game) == before
    if resolution == "delete":
        await f.events.delete_event(8802, e.id)
    else:
        await f.events.update_event(8802, e.id, player_id=f.players[2], event_type=EventType.IQ_200)
    await edit(f, participants=f.players[:4] + (f.players[5],))
    assert f.players[5] in (await f.history.card(8802, f.game)).roles


@pytest.mark.asyncio
async def test_void_visibility_and_original_sequence_not_reused(history_data):
    f = history_data
    old = await f.history.card(8802, f.game)
    await void(f)
    assert (await f.history.list_games(8801, f.club)).total == 0
    assert (await f.history.list_games(8802, f.club)).items[0].status is GameStatus.VOID
    with pytest.raises(GameError, match="NO_ACCESS"):
        await f.history.card(8801, f.game)
    with pytest.raises(GameError, match="NO_ACCESS"):
        await f.games.game(8801, f.game)
    assert (await f.history.card(8802, f.game)).game.sequence_number == old.game.sequence_number == 1
    second, _ = await f.games.create_game(8801, f.evening, uuid4(), f.players[:5], f.players[0], (f.players[1],),
                                          Team.LIBERAL, VictoryType.HITLER_KILLED)
    assert second.sequence_number == 2
    await void(f, restore=True)
    assert (await f.history.card(8802, f.game)).game.sequence_number == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("restore", [False, True])
@pytest.mark.parametrize("actor", [8801, 8902])
async def test_nonadmin_and_other_club_cannot_void_or_restore(history_data, actor, restore):
    f = history_data
    if restore:
        await void(f)
    with pytest.raises(GameError, match="NO_ACCESS"):
        await void(f, actor=actor, restore=restore)


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["roles", "victory"])
async def test_restore_revalidates_corrupt_snapshot_without_partial_restore(history_data, corruption):
    f = history_data
    await void(f)
    async with f.factory() as db, db.begin():
        if corruption == "roles":
            await db.execute(update(GamePlayer).where(GamePlayer.game_id == f.game, GamePlayer.role == GameRole.HITLER)
                             .values(role=GameRole.LIBERAL))
        else:
            (await db.get(Game, f.game)).victory_type = VictoryType.HITLER_KILLED
    with pytest.raises(GameError, match="INVALID_ROLES|INVALID_VICTORY"):
        await void(f, restore=True)
    assert (await f.history.card(8802, f.game)).game.status is GameStatus.VOID
    assert [r.action for r in await audit_rows(f)] == ["GAME_VOID"]


@pytest.mark.asyncio
async def test_legacy_unknown_restore_and_correction_do_not_guess_victory(history_data):
    f = history_data
    async with f.factory() as db, db.begin():
        (await db.get(Game, f.game)).victory_type = VictoryType.UNKNOWN
    await void(f)
    await void(f, restore=True)
    await edit(f, victory=VictoryType.UNKNOWN)
    assert (await f.history.card(8802, f.game)).victory_type is VictoryType.UNKNOWN


@pytest.mark.asyncio
async def test_edit_and_void_revisions_prevent_lost_updates_and_old_restore(history_data):
    f = history_data
    old = (await f.history.card(8802, f.game)).revision
    await edit(f)
    for operation in (lambda: edit(f, revision=old), lambda: void(f, revision=old)):
        with pytest.raises(GameError, match="STALE"):
            await operation()
    await void(f)
    void_rev = (await f.history.card(8802, f.game)).revision
    await void(f, restore=True)
    with pytest.raises(GameError, match="STALE"):
        await void(f, restore=True, revision=void_rev)


@pytest.mark.asyncio
async def test_player_rename_deactivate_reactivate_audit_and_preserved_history(history_data):
    f = history_data
    e = await moment(f, f.players[4])
    _, p = await f.history.player(8802, f.players[4])
    before = await f.stats.get_player_stats(8802, f.club, p.id)
    p = await f.history.edit_player(8802, p.id, expected_revision=p.revision, display_name=" New <name> ")
    assert p.name == "New <name>"
    assert next(person.name for person in (await f.history.card(8801, f.game)).participants if person.id == p.id) == p.name
    p = await f.history.edit_player(8802, p.id, expected_revision=p.revision, is_active=False)
    assert not p.is_active
    assert (await f.stats.get_player_stats(8802, f.club, p.id)).total_games == before.total_games
    assert (await f.history.list_games(8801, f.club, player_id=p.id)).total == 1
    assert (await f.events.event(8801, e.id)).player_name == p.name
    assert p.id not in {person.id for person in (await f.games.setup(8801, f.evening)).candidates}
    with pytest.raises(GameError, match="INVALID_PARTICIPANTS"):
        await f.games.create_game(8801, f.evening, uuid4(), f.players[:5], f.players[0], (f.players[1],),
                                 Team.LIBERAL, VictoryType.HITLER_KILLED)
    with pytest.raises(SessionError):
        await f.sessions.add_player(8801, f.evening, p.id)
    p = await f.history.edit_player(8802, p.id, expected_revision=p.revision, is_active=True)
    assert p.is_active
    assert p.id in {person.id for person in (await f.games.setup(8801, f.evening)).candidates}
    rows = await audit_rows(f)
    assert [r.action for r in rows] == ["PLAYER_EDIT"] * 3
    assert rows[0].before_data["display_name"] != rows[0].after_data["display_name"]
    assert rows[1].after_data["is_active"] is False and rows[2].after_data["is_active"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["", "   ", "x" * 256])
async def test_invalid_player_name_and_permissions(history_data, name):
    f = history_data
    _, p = await f.history.player(8802, f.players[2])
    with pytest.raises(GameError, match="INVALID_NAME"):
        await f.history.edit_player(8802, p.id, expected_revision=p.revision, display_name=name)
    for actor in (8801, 8902):
        with pytest.raises(GameError, match="NO_ACCESS"):
            await f.history.edit_player(actor, p.id, expected_revision=p.revision, is_active=False)
    assert not await audit_rows(f)


@pytest.mark.asyncio
async def test_event_edit_delete_permissions_and_revisions_remain_audited(history_data):
    f = history_data
    e = await moment(f)
    _, _, rev = await f.events.edit_setup(8801, e.id)
    await f.events.update_event(8801, e.id, player_id=e.player_id, event_type=EventType.PALIVO, expected_revision=rev)
    with pytest.raises(EventError, match="STALE"):
        await f.events.update_event(8802, e.id, player_id=e.player_id, event_type=EventType.PROPHET, expected_revision=rev)
    with pytest.raises(EventError, match="NO_ACCESS"):
        await f.events.delete_event(8801, e.id)
    async with f.factory() as db, db.begin():
        (await db.get(GameSession, f.evening)).status = SessionStatus.FINISHED
    with pytest.raises(EventError, match="NO_ACCESS"):
        await f.events.edit_setup(8801, e.id)
    _, _, rev = await f.events.edit_setup(8802, e.id)
    await f.events.update_event(8802, e.id, player_id=e.player_id, event_type=EventType.PROPHET, expected_revision=rev)
    _, _, rev = await f.events.edit_setup(8802, e.id)
    await f.events.delete_event(8802, e.id, expected_revision=rev)
    assert [r.action for r in await audit_rows(f)] == ["EVENT_EDIT", "EVENT_EDIT", "EVENT_DELETE"]


@pytest.mark.asyncio
@pytest.mark.parametrize("api", ["sessions", "games", "session_id", "player_id", "card", "edit", "void", "restore", "player", "players", "audit", "event"])
async def test_multi_club_history_mutations_and_audit_are_isolated(history_data, api):
    f = history_data
    other_card = await f.history.card(8902, f.other_game)
    calls = {
        "sessions": lambda: f.history.list_sessions(8802, f.other_club),
        "games": lambda: f.history.list_games(8802, f.other_club),
        "session_id": lambda: f.history.list_games(8802, f.club, session_id=f.other_evening),
        "player_id": lambda: f.history.list_games(8802, f.club, player_id=f.others[0]),
        "card": lambda: f.history.card(8802, f.other_game),
        "edit": lambda: edit(f, actor=8802, game=f.other_game),
        "void": lambda: void(f, actor=8802, game=f.other_game),
        "restore": lambda: f.games.set_game_void(8802, f.other_game, expected_revision=other_card.revision, restore=True),
        "player": lambda: f.history.edit_player(8802, f.others[0], expected_revision="forged", is_active=False),
        "players": lambda: f.history.list_players(8802, f.other_club, admin=True),
        "audit": lambda: f.history.list_audit(8802, f.other_club),
    }
    if api == "event":
        e, _ = await f.events.create_event(8901, f.other_game, uuid4(), f.others[0], EventType.IQ_200)
        with pytest.raises(EventError, match="NO_ACCESS"):
            await f.events.delete_event(8802, e.id)
    else:
        with pytest.raises(GameError):
            await calls[api]()


@pytest.mark.asyncio
async def test_audit_details_admin_only_and_reads_do_not_write(history_data):
    f = history_data
    await edit(f)
    entry = (await f.history.list_audit(8802, f.club)).items[0]
    assert (await f.history.audit(8802, entry.id))[1] == entry
    assert entry.actor_name == "Player 01"
    for actor in (8801, 8902):
        with pytest.raises(GameError, match="NO_ACCESS"):
            await f.history.audit(actor, entry.id)
        with pytest.raises(GameError, match="NO_ACCESS"):
            await f.history.list_audit(actor, f.club)
    before = len(await audit_rows(f))
    await f.history.list_sessions(8801, f.club)
    await f.history.list_games(8801, f.club)
    await f.history.list_players(8801, f.club)
    await f.history.card(8801, f.game)
    assert len(await audit_rows(f)) == before


@pytest.mark.asyncio
async def test_all_history_lists_paginate_and_game_page_has_constant_query_count(history_data):
    f = history_data
    for _ in range(12):
        await f.games.create_game(8801, f.evening, uuid4(), f.players[:5], f.players[0], (f.players[1],), Team.LIBERAL, VictoryType.HITLER_KILLED)
    async with f.factory() as db, db.begin():
        db.add_all(GameSession(club_id=f.club, status=SessionStatus.FINISHED, created_by_player_id=f.players[0],
                              statistician_player_id=f.players[0]) for _ in range(12))
        db.add_all(Player(club_id=f.club, display_name=f"Extra {i}") for i in range(12))
    for i in range(12):
        _, p = await f.history.player(8802, f.players[4])
        await f.history.edit_player(8802, p.id, expected_revision=p.revision, display_name=f"Name {i}")
    queries = []
    def record(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("SELECT"):
            queries.append(statement)
    event.listen(f.engine.sync_engine, "before_cursor_execute", record)
    try:
        first = await f.history.list_games(8801, f.club)
        assert len(queries) == 3
    finally:
        event.remove(f.engine.sync_engine, "before_cursor_execute", record)
    assert first.total == 13 and len(first.items) == 10
    assert (await f.history.list_games(8801, f.club, page=99)).page == 1
    assert (await f.history.list_games(8801, f.club, player_id=f.players[0], page=1)).total == 13
    assert (await f.history.list_sessions(8801, f.club, page=1)).total == 13
    assert (await f.history.list_players(8802, f.club, page=2)).total == 22
    assert (await f.history.list_audit(8802, f.club, page=1)).total == 12
    for _ in range(12):
        await moment(f)
    assert (await f.history.card(8801, f.game)).events.total == 12
    assert len((await f.history.card(8801, f.game, event_page=99)).events.items) == 2


@pytest.mark.asyncio
async def test_void_session_hidden_from_player_and_visible_to_admin(history_data):
    f = history_data
    async with f.factory() as db, db.begin():
        (await db.get(GameSession, f.evening)).status = SessionStatus.VOID
    assert (await f.history.list_sessions(8801, f.club)).total == 0
    assert (await f.history.list_games(8801, f.club)).total == 0
    assert (await f.history.list_sessions(8802, f.club)).total == 1
    with pytest.raises(GameError):
        await f.history.object_club(8801, "session", f.evening)
    assert (await f.history.card(8802, f.game)).is_admin


@pytest.mark.asyncio
async def test_telegram_historical_correction_void_restore_changes_real_stats(history_data, monkeypatch):
    from unittest.mock import AsyncMock
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.storage.memory import MemoryStorage
    from aiogram.types import CallbackQuery, Chat, Message, User
    from app.bot.callbacks import GameCard, HistoryMutation, HistoryPage, HistoryStep
    from app.bot.handlers import history as ui
    f = history_data
    async with f.factory() as db, db.begin():
        (await db.get(GameSession, f.evening)).status = SessionStatus.FINISHED
    storage = MemoryStorage()
    state = FSMContext(storage, StorageKey(bot_id=1, chat_id=8802, user_id=8802))
    user = User(id=8802, first_name="Admin", is_bot=False)
    msg = Message(message_id=1, date=datetime.now(timezone.utc), chat=Chat(id=8802, type="private"), from_user=user)
    cb = CallbackQuery(id="test", from_user=user, chat_instance="test", message=msg)
    answer = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer)
    monkeypatch.setattr(CallbackQuery, "answer", AsyncMock())
    async def start(intent):
        rev = (await f.history.card(8802, f.game)).revision
        await ui.begin(cb, HistoryMutation(entity_id=f.game, action=intent, revision=rev), state, f.history, f.events)
    async def tap(action):
        flow = (await state.get_data())["flow"]
        await ui.step(cb, HistoryStep(flow=flow, action=action), state, f.history, f.games, f.events)
    try:
        await ui.page(cb, HistoryPage(scope_id=f.club, section="sessions"), f.history, f.stats, f.events)
        await ui.page(cb, HistoryPage(scope_id=f.evening, section="session"), f.history, f.stats, f.events)
        await ui.game_card(cb, GameCard(game_id=f.game), f.history)
        await start("ge")
        await tap("winner")
        await tap("win_L")
        await tap("v_LIBERAL_POLICIES")
        assert (await f.stats.get_club_stats(8801, f.club)).score.fascist_wins == 1  # draft only
        await tap("save")
        assert (await f.stats.get_club_stats(8801, f.club)).score.liberal_wins == 1
        await start("gv")
        await tap("confirm")
        assert (await f.stats.get_club_stats(8801, f.club)).score.games_count == 0
        await start("gr")
        await tap("confirm")
        assert (await f.stats.get_club_stats(8801, f.club)).score.games_count == 1
        assert [r.action for r in await audit_rows(f)] == ["GAME_EDIT", "GAME_VOID", "GAME_RESTORE"]
        assert all(c.args[0] != ru.GENERIC_ERROR for c in answer.await_args_list)
    finally:
        await storage.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["edit", "void", "restore", "event", "participant_event"])
async def test_real_postgresql_concurrent_corrections_are_serialized(test_database_url, operation):
    from sqlalchemy import delete
    from app.db.models import Club, SessionParticipant, TelegramUser
    engine = create_async_engine(test_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tids = (uuid4().int % 10_000_000_000, uuid4().int % 10_000_000_000)
    club = evening = None
    try:
        club, evening, players, users = await seed(factory, telegram_ids=tids)
        games, history, events = GameService(factory), HistoryService(factory), EventService(factory)
        g, _ = await games.create_game(tids[0], evening, uuid4(), players[:5], players[0], (players[1],), Team.FASCIST, VictoryType.FASCIST_POLICIES)
        if operation == "restore":
            await games.set_game_void(tids[1], g.id, expected_revision=(await history.card(tids[1], g.id)).revision)
        rev = (await history.card(tids[1], g.id)).revision
        async def edit_one():
            return await games.edit_game(tids[1], g.id, expected_revision=rev,
                participants=players[:4] + (players[5],) if operation == "participant_event" else players[:5],
                hitler=players[0], fascists=(players[1],), winner=Team.LIBERAL, victory_type=VictoryType.LIBERAL_POLICIES)
        if operation == "event":
            e, _ = await events.create_event(tids[0], g.id, uuid4(), players[1], EventType.IQ_200)
            _, _, erev = await events.edit_setup(tids[1], e.id)
            async def change_event():
                return await events.update_event(tids[1], e.id, player_id=players[1], event_type=EventType.PALIVO,
                                                  expected_revision=erev)
            calls = [change_event(), change_event()]
        elif operation == "participant_event":
            calls = [edit_one(), events.create_event(tids[0], g.id, uuid4(), players[4], EventType.IQ_200)]
        elif operation == "edit":
            calls = [edit_one(), edit_one()]
        else:
            calls = [games.set_game_void(tids[1], g.id, expected_revision=rev, restore=operation == "restore") for _ in range(2)]
        results = await asyncio.wait_for(asyncio.gather(*calls, return_exceptions=True), timeout=10)
        assert sum(not isinstance(r, Exception) for r in results) == 1
        errors = [r for r in results if isinstance(r, Exception)]
        assert all(isinstance(r, (GameError, EventError)) for r in errors)
        assert errors[0].code.value in ("STALE", "EVENT_CONFLICT", "INVALID_EVENT")
        card = await history.card(tids[1], g.id)
        assert all(e.player_id in card.roles for e in card.events.items)
        async with factory() as db:
            if operation != "participant_event":
                assert await db.scalar(select(func.count(AuditLog.id)).where(AuditLog.club_id == club,
                                       AuditLog.action == {"edit": "GAME_EDIT", "void": "GAME_VOID", "restore": "GAME_RESTORE", "event": "EVENT_EDIT"}[operation])) == 1
    finally:
        if club:
            async with factory() as db, db.begin():
                await db.execute(delete(AuditLog).where(AuditLog.club_id == club))
                await db.execute(delete(GameEvent).where(GameEvent.game_id.in_(select(Game.id).where(Game.session_id == evening))))
                await db.execute(delete(GamePlayer).where(GamePlayer.game_id.in_(select(Game.id).where(Game.session_id == evening))))
                await db.execute(delete(Game).where(Game.session_id == evening))
                await db.execute(delete(SessionParticipant).where(SessionParticipant.session_id == evening))
                await db.execute(delete(GameSession).where(GameSession.id == evening))
                await db.execute(delete(ClubMembership).where(ClubMembership.club_id == club))
                await db.execute(delete(Player).where(Player.club_id == club))
                await db.execute(delete(TelegramUser).where(TelegramUser.id.in_(users)))
                await db.execute(delete(Club).where(Club.id == club))
        await engine.dispose()


@pytest.mark.asyncio
async def test_inactive_player_is_excluded_from_future_evening_and_statistician_candidates(history_data):
    f = history_data
    _, p = await f.history.player(8802, f.players[1])
    await f.history.edit_player(8802, p.id, expected_revision=p.revision, is_active=False)
    with pytest.raises(SessionError):
        await f.sessions.transfer(8801, f.evening, p.id)
    assert p.id not in {p.id for p in await f.sessions.recovery_choices(8801, f.evening)}
    await f.sessions.finish(8801, f.evening)
    assert p.id not in {p.id for p in await f.sessions.active_players(8801, f.club)}
    with pytest.raises(SessionError, match="INVALID_SELECTION"):
        await f.sessions.start(8801, f.club, f.players[:5])
    assert (await f.stats.get_player_stats(8802, f.club, p.id)).total_games == 1


@pytest.mark.asyncio
async def test_game_edit_keeps_identity_and_roster_independent(history_data):
    f = history_data
    async with f.factory() as db:
        game = await db.get(Game, f.game)
        identity = (game.session_id, game.sequence_number, game.created_at, game.played_at)
    await edit(f)
    async with f.factory() as db:
        game = await db.get(Game, f.game)
        assert (game.session_id, game.sequence_number, game.created_at, game.played_at) == identity


@pytest.mark.asyncio
async def test_event_and_player_callbacks_scope_and_player_revision(history_data):
    f = history_data
    event, _ = await f.events.create_event(8901, f.other_game, uuid4(), f.others[0], EventType.IQ_200)
    for kind, obj in (("event", event.id), ("player", f.others[0]), ("session", f.other_evening)):
        with pytest.raises(GameError, match="NO_ACCESS"):
            await f.history.object_club(8802, kind, obj, admin=True)
    _, player = await f.history.player(8802, f.players[3])
    await f.history.edit_player(8802, player.id, expected_revision=player.revision, display_name="Updated")
    with pytest.raises(GameError, match="STALE"):
        await f.history.edit_player(8802, player.id, expected_revision=player.revision, is_active=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("page", [-1, True])
async def test_invalid_history_page_is_safe(history_data, page):
    with pytest.raises(GameError, match="STALE"):
        await history_data.history.list_games(8801, history_data.club, page=page)
