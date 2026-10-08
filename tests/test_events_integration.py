"""Events on migrated PostgreSQL; official games and their snapshots remain unchanged."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message, User

from test_games_integration import seed, test_database_url
from app.db.models import AuditLog, Club, ClubMembership, Game, GameEvent, GamePlayer, GameSession, Player, SessionParticipant, TelegramUser
from app.domain.enums import EventType, MembershipRole, MembershipStatus, RuinReason, SessionStatus, Team, VictoryType
from app.services.events import EventError, EventErrorCode, EventService
from app.services.games import GameService
from app.services.sessions import SessionService
from app.bot.callbacks import EventChoice, EventPick, EventAction, EventSave
from app.bot.handlers.events import begin_event, choose, pick_player, action, save
from app.locales import ru


@pytest_asyncio.fixture
async def events(test_database_url):
    engine = create_async_engine(test_database_url)
    async with engine.connect() as connection:
        tx = await connection.begin()
        factory = async_sessionmaker(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
        club, evening, players, _ = await seed(factory)
        async with factory() as db, db.begin():
            user = TelegramUser(telegram_user_id=8803)
            db.add(user)
            await db.flush()
            player = await db.get(Player, players[2])
            player.telegram_user_id = user.id
            db.add(ClubMembership(club_id=club, telegram_user_id=user.id,
                                  role=MembershipRole.PLAYER, status=MembershipStatus.ACTIVE))
        game_service = GameService(factory)
        game, _ = await game_service.create_game(8801, evening, uuid4(), players[:5], players[0],
                            (players[1],), Team.LIBERAL, VictoryType.HITLER_KILLED)
        yield EventService(factory), game_service, SessionService(factory), factory, club, evening, players, game
        await tx.rollback()
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", list(EventType))
async def test_create_each_event_type(events, event_type):
    service, _, _, factory, _, _, players, game = events
    event, created = await service.create_event(8801, game.id, uuid4(), players[0], event_type,
                          RuinReason.WRONG_VOTE if event_type is EventType.RUIN else None, True)
    assert created and event.event_type is event_type
    assert event.self_admitted is (event_type is EventType.RUIN)
    assert event.comment is None
    assert len(await service.list_game_events(8803, game.id)) == 1
    async with factory() as db:
        row = await db.get(GameEvent, event.id)
        assert row.created_by_player_id == players[0]
        assert row.created_at.tzinfo is not None
    # RUIN is also accepted for a player whose team won.
    if event_type is EventType.RUIN:
        await service.create_event(8801, game.id, uuid4(), players[2], EventType.RUIN, RuinReason.OTHER)


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", list(RuinReason))
async def test_each_reason_persists(events, reason):
    service, _, _, _, _, _, players, game = events
    event, _ = await service.create_event(8801, game.id, uuid4(), players[2], EventType.RUIN, reason)
    assert event.ruin_reason is reason and not event.self_admitted


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [EventType.PALIVO, EventType.IQ_200, EventType.PROPHET])
async def test_non_ruin_fields_normalized_in_database(events, kind):
    service, _, _, factory, _, _, players, game = events
    event, _ = await service.create_event(8801, game.id, uuid4(), players[0], kind, RuinReason.OTHER, True)
    async with factory() as db:
        row = await db.get(GameEvent, event.id)
        assert row.ruin_reason is None and row.self_admitted is False


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,reason,comment", [(EventType.RUIN, None, None),
                                              (EventType.PALIVO, None, "x" * 501),
                                              ("INVALID", None, None)])
async def test_service_rejects_invalid_fields_without_partial_write(events, kind, reason, comment):
    service, _, _, factory, _, _, players, game = events
    with pytest.raises(EventError) as error:
        await service.create_event(8801, game.id, uuid4(), players[0], kind, reason, comment=comment)
    assert error.value.code is EventErrorCode.INVALID_EVENT
    async with factory() as db:
        assert await db.scalar(select(func.count(GameEvent.id))) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("comment", [None, "x" * 500, " \n "])
async def test_comments(events, comment):
    service, _, _, _, _, _, players, game = events
    event, _ = await service.create_event(8801, game.id, uuid4(), players[0], EventType.PROPHET, comment=comment)
    assert event.comment == (comment.strip() or None if comment is not None else None)


@pytest.mark.asyncio
async def test_participants_come_from_game_snapshot(events):
    service, _, sessions, factory, _, evening, players, game = events
    await sessions.remove_player(8801, evening, players[2])
    setup = await service.setup(8801, game.id)
    assert {p.id for p in setup.participants} == set(players[:5])
    await service.create_event(8801, game.id, uuid4(), players[2], EventType.PROPHET)
    for pid in (players[5], uuid4()):
        with pytest.raises(EventError) as error:
            await service.create_event(8801, game.id, uuid4(), pid, EventType.PROPHET)
        assert error.value.code is EventErrorCode.INVALID_EVENT
    async with factory() as db, db.begin():
        other = Club(name="Other", timezone="UTC")
        db.add(other)
        await db.flush()
        outsider = Player(club_id=other.id, display_name="Other")
        db.add(outsider)
        await db.flush()
        outsider_id = outsider.id
    with pytest.raises(EventError) as error:
        await service.create_event(8801, game.id, uuid4(), outsider_id, EventType.PROPHET)
    assert error.value.code is EventErrorCode.INVALID_EVENT


@pytest.mark.asyncio
async def test_multiple_events_and_repeat_save(events):
    service, _, _, _, _, _, players, game = events
    first_id = uuid4()
    first, created = await service.create_event(8801, game.id, first_id, players[0], EventType.RUIN, RuinReason.OTHER)
    again, repeated = await service.create_event(8801, game.id, first_id, players[0], EventType.RUIN, RuinReason.OTHER)
    assert created and not repeated and first.id == again.id
    await service.create_event(8801, game.id, uuid4(), players[0], EventType.RUIN, RuinReason.OTHER)
    for kind in (EventType.PALIVO, EventType.IQ_200, EventType.PROPHET):
        await service.create_event(8801, game.id, uuid4(), players[0], kind)
    await service.create_event(8801, game.id, uuid4(), players[2], EventType.PROPHET)
    assert len(await service.list_game_events(8802, game.id)) == 6


@pytest.mark.asyncio
@pytest.mark.parametrize("telegram_id", [8803, 9999])
async def test_player_and_outsider_cannot_create(events, telegram_id):
    service, _, _, _, _, _, players, game = events
    with pytest.raises(EventError) as error:
        await service.create_event(telegram_id, game.id, uuid4(), players[0], EventType.PROPHET)
    assert error.value.code is EventErrorCode.NO_ACCESS


@pytest.mark.asyncio
async def test_transfer_and_finish_recheck_permissions(events):
    service, _, sessions, factory, _, evening, players, game = events
    await sessions.transfer(8801, evening, players[2])
    with pytest.raises(EventError) as error:
        await service.create_event(8801, game.id, uuid4(), players[0], EventType.PROPHET)
    assert error.value.code is EventErrorCode.NO_ACCESS
    event, _ = await service.create_event(8803, game.id, uuid4(), players[0], EventType.PROPHET)
    with pytest.raises(EventError) as error:
        await service.update_event(8801, event.id, player_id=players[0], event_type=EventType.IQ_200)
    assert error.value.code is EventErrorCode.NO_ACCESS
    await service.create_event(8803, game.id, uuid4(), players[0], EventType.PROPHET)
    await sessions.finish(8803, evening)
    with pytest.raises(EventError) as error:
        await service.create_event(8803, game.id, uuid4(), players[0], EventType.PROPHET)
    assert error.value.code is EventErrorCode.NO_ACCESS
    historical, _ = await service.create_event(8802, game.id, uuid4(), players[0], EventType.PROPHET)
    async with factory() as db:
        audit = await db.scalar(select(AuditLog).where(AuditLog.entity_id == historical.id))
        assert audit.action == "EVENT_CREATE" and audit.actor_player_id == players[1]
        assert audit.before_data is None and audit.after_data["game_id"] == str(game.id)


@pytest.mark.asyncio
async def test_admin_edits_all_fields_and_audits(events):
    service, _, sessions, factory, _, evening, players, game = events
    first, _ = await service.create_event(8801, game.id, uuid4(), players[0], EventType.RUIN, RuinReason.OTHER, True, "old")
    await sessions.finish(8801, evening)
    changed = await service.update_event(8802, first.id, player_id=players[2], event_type=EventType.RUIN,
                                         ruin_reason=RuinReason.BAD_EXECUTION, self_admitted=False, comment="new")
    assert changed.player_id == players[2] and changed.ruin_reason is RuinReason.BAD_EXECUTION
    assert changed.comment == "new" and not changed.self_admitted
    changed = await service.update_event(8802, first.id, player_id=players[2], event_type=EventType.IQ_200,
                                         ruin_reason=RuinReason.OTHER, self_admitted=True)
    assert changed.ruin_reason is None and not changed.self_admitted
    with pytest.raises(EventError) as error:
        await service.update_event(8802, first.id, player_id=players[2], event_type=EventType.RUIN)
    assert error.value.code is EventErrorCode.INVALID_EVENT
    async with factory() as db:
        audits = (await db.scalars(select(AuditLog).where(AuditLog.entity_id == first.id)
                                  .order_by(AuditLog.created_at, AuditLog.id))).all()
        assert len(audits) == 2 and all(a.action == "EVENT_EDIT" for a in audits)
        assert any(a.before_data["comment"] == "old" and a.after_data["comment"] == "new" for a in audits)
        assert all(a.actor_player_id == players[1] and a.created_at.tzinfo for a in audits)
        assert (await db.get(GameEvent, first.id)).event_type is EventType.IQ_200


@pytest.mark.asyncio
@pytest.mark.parametrize("telegram_id,finished", [(8803, False), (8801, True)])
async def test_non_admin_cannot_edit_or_delete(events, telegram_id, finished):
    service, _, sessions, _, _, evening, players, game = events
    event, _ = await service.create_event(8801, game.id, uuid4(), players[0], EventType.PROPHET)
    if finished:
        await sessions.finish(8801, evening)
    for operation in (service.update_event(telegram_id, event.id, player_id=players[0], event_type=EventType.IQ_200),
                      service.delete_event(telegram_id, event.id)):
        with pytest.raises(EventError) as error:
            await operation
        assert error.value.code is EventErrorCode.NO_ACCESS


@pytest.mark.asyncio
async def test_admin_delete_is_audited_and_save_cannot_resurrect(events):
    service, _, _, factory, _, _, players, game = events
    event, _ = await service.create_event(8802, game.id, uuid4(), players[0], EventType.RUIN, RuinReason.OTHER, True, "note")
    await service.delete_event(8802, event.id)
    assert await service.list_game_events(8802, game.id) == ()
    with pytest.raises(EventError) as error:
        await service.create_event(8802, game.id, event.id, players[0], EventType.RUIN, RuinReason.OTHER)
    assert error.value.code is EventErrorCode.DRAFT_CONFLICT
    async with factory() as db:
        audit = await db.scalar(select(AuditLog).where(AuditLog.entity_id == event.id, AuditLog.action == "EVENT_DELETE"))
        assert audit.before_data["comment"] == "note" and audit.before_data["self_admitted"] is True
        assert audit.before_data["ruin_reason"] == "OTHER" and audit.after_data is None
        assert audit.entity_type == "GameEvent" and audit.actor_player_id == players[1]
        assert await db.get(Game, game.id) is not None


@pytest.mark.asyncio
async def test_statistician_can_correct_current_event_but_cannot_delete(events):
    service, _, _, _, _, _, players, game = events
    event, _ = await service.create_event(8801, game.id, uuid4(), players[0], EventType.PROPHET)
    edited = await service.update_event(8801, event.id, player_id=players[2], event_type=EventType.IQ_200)
    assert edited.event_type is EventType.IQ_200 and edited.player_id == players[2]
    with pytest.raises(EventError) as error:
        await service.delete_event(8801, event.id)
    assert error.value.code is EventErrorCode.NO_ACCESS


@pytest.mark.asyncio
async def test_foreign_club_cannot_read_or_mutate(events):
    service, _, _, factory, _, _, players, game = events
    event, _ = await service.create_event(8801, game.id, uuid4(), players[0], EventType.PROPHET)
    async with factory() as db, db.begin():
        club, user = Club(name="Foreign", timezone="UTC"), TelegramUser(telegram_user_id=8804)
        db.add_all([club, user])
        await db.flush()
        db.add_all([ClubMembership(club_id=club.id, telegram_user_id=user.id, role=MembershipRole.ADMIN,
                                  status=MembershipStatus.ACTIVE),
                    Player(club_id=club.id, telegram_user_id=user.id, display_name="Admin")])
    operations = [service.setup(8804, game.id), service.list_game_events(8804, game.id),
                  service.event(8804, event.id), service.create_event(8804, game.id, uuid4(), players[0], EventType.PROPHET),
                  service.update_event(8804, event.id, player_id=players[0], event_type=EventType.PROPHET),
                  service.delete_event(8804, event.id)]
    for operation in operations:
        with pytest.raises(EventError) as error:
            await operation
        assert error.value.code is EventErrorCode.NO_ACCESS


@pytest.mark.asyncio
async def test_concurrent_event_saves(test_database_url):
    engine = create_async_engine(test_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tids = (int(uuid4().int % 10_000_000_000), int(uuid4().int % 10_000_000_000))
    club = None
    try:
        club, evening, players, users = await seed(factory, telegram_ids=tids)
        game, _ = await GameService(factory).create_game(tids[0], evening, uuid4(), players[:5], players[0],
                                (players[1],), Team.LIBERAL, VictoryType.HITLER_KILLED)
        service, draft = EventService(factory), uuid4()
        results = await asyncio.gather(*(service.create_event(tids[1], game.id, draft, players[0], EventType.PROPHET)
                                         for _ in range(2)))
        assert {event.id for event, _ in results} == {draft}
        assert sorted(created for _, created in results) == [False, True]
        distinct = await asyncio.gather(*(service.create_event(tids[1], game.id, uuid4(), players[0], EventType.PROPHET)
                                          for _ in range(2)))
        assert all(created for _, created in distinct)
        async with factory() as db:
            assert await db.scalar(select(func.count(GameEvent.id)).where(GameEvent.game_id == game.id)) == 3
            assert await db.scalar(select(func.count(AuditLog.id)).where(AuditLog.club_id == club)) == 3
    finally:
        if club is not None:
            async with factory() as db, db.begin():
                await db.execute(delete(GameEvent).where(GameEvent.game_id.in_(select(Game.id).where(Game.session_id == evening))))
                await db.execute(delete(AuditLog).where(AuditLog.club_id == club))
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
@pytest.mark.parametrize("kind", list(EventType))
async def test_event_wizard_inserts_once_only_after_review(events, monkeypatch, kind):
    service, games, _, factory, _, _, players, game = events
    storage = MemoryStorage()
    state = FSMContext(storage, StorageKey(bot_id=1, chat_id=8801, user_id=8801))
    answer = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer)
    monkeypatch.setattr(CallbackQuery, "answer", AsyncMock())
    message = Message(message_id=1, date=datetime.now(timezone.utc), chat=Chat(id=8801, type="private"),
                      from_user=User(id=8801, is_bot=False, first_name="Stat"))
    callback = CallbackQuery(id="test", from_user=message.from_user, chat_instance="test", message=message)
    try:
        await begin_event(callback, state, service, game.id)
        await choose(callback, EventChoice(flow=(await state.get_data())["flow"], kind="t", code=kind.value), state)
        await pick_player(callback, EventPick(flow=(await state.get_data())["flow"], player_id=players[2]), state)
        if kind is EventType.RUIN:
            await choose(callback, EventChoice(flow=(await state.get_data())["flow"], kind="r", code="OTHER"), state)
            await choose(callback, EventChoice(flow=(await state.get_data())["flow"], kind="a", code="n"), state)
        await action(callback, EventAction(flow=(await state.get_data())["flow"], action="skip"), state, games, service)
        assert ru.EVENT_REVIEW in answer.await_args.args[0]
        assert await service.list_game_events(8801, game.id) == ()
        current = await state.get_data()
        button = EventSave(flow=current["flow"], draft_id=UUID(current["draft_id"]))
        await save(callback, button, state, service)
        await save(callback, button, state, service)
        assert ru.EVENT_SAVED in answer.await_args.args[0]
        assert len(await service.list_game_events(8801, game.id)) == 1
        async with factory() as db:
            row = await db.get(GameEvent, button.draft_id)
            assert row.event_type is kind and row.player_id == players[2]
        await begin_event(callback, state, service, game.id)
        before = await state.get_data()
        await save(callback, button, state, service)
        assert await state.get_data() == before
        assert answer.await_args.args[0] == ru.EVENT_ERRORS["STALE"]
        assert len(await service.list_game_events(8801, game.id)) == 1
    finally:
        await storage.close()
