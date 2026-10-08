"""History FSM, entity revisions, read-only cards and real aiogram routing."""

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from app.bot.callbacks import EventAction, EventChoice, EventPick, EventSave, GameCard, HistoryMutation, HistoryPage, HistoryPick, HistoryStep
from app.bot.handlers import history as handlers
from app.bot.handlers.events import EventFlow, action as event_action, choose, pick_player, save as event_save
from app.bot.keyboards import history as keyboards
from app.domain.enums import EventType, GameRole, GameStatus, MembershipRole, RuinReason, SessionStatus, Team, VictoryType
from app.locales import ru
from app.main import create_dispatcher
from app.services.events import EventSetup, EventView
from app.services.games import GameError, GameErrorCode, GamePerson
from app.services.history import AuditEntry, HistoryCard, HistoryGame, HistoryPlayer, HistorySession
from app.services.stats import Page

CLUB, GAME, EVENING, EVENT = (UUID(int=i) for i in (101, 102, 103, 104))
PEOPLE = tuple(GamePerson(UUID(int=i), f"Player <{i}>") for i in range(1, 7))
REV = "0123456789abcdef"
STAMP = datetime(2026, 10, 8, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def ui(monkeypatch):
    storage = MemoryStorage()
    state = FSMContext(storage, StorageKey(bot_id=1, chat_id=8802, user_id=8802))
    user = User(id=8802, first_name="Admin", is_bot=False)
    msg = Message(message_id=1, date=STAMP, chat=Chat(id=8802, type="private"), from_user=user)
    callback = CallbackQuery(id="test", from_user=user, chat_instance="test", message=msg)
    counter = 10
    async def send(*args, **kwargs):
        nonlocal counter
        counter += 1
        return msg.model_copy(update={"message_id": counter})
    answer = AsyncMock(side_effect=send)
    monkeypatch.setattr(Message, "answer", answer)
    monkeypatch.setattr(CallbackQuery, "answer", AsyncMock())
    history, games, events, stats = AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock()
    event = EventView(EVENT, GAME, PEOPLE[1].id, PEOPLE[1].name, EventType.RUIN, RuinReason.OTHER, True, "<b>comment</b>")
    g = HistoryGame(GAME, EVENING, 1, STAMP, "UTC", Team.FASCIST, GameStatus.ACTIVE, 5)
    roles = {PEOPLE[0].id: GameRole.HITLER, PEOPLE[1].id: GameRole.FASCIST,
             **{p.id: GameRole.LIBERAL for p in PEOPLE[2:5]}}
    card = HistoryCard(g, CLUB, VictoryType.FASCIST_POLICIES, PEOPLE[:5], roles, Page((event,), 0, 1), REV, True, True, True)
    history.card.return_value = card
    history.edit_candidates.return_value = PEOPLE
    history.list_games.return_value = Page((g,), 0, 1)
    history.list_sessions.return_value = Page((HistorySession(EVENING, STAMP, "UTC", SessionStatus.FINISHED, 1),), 0, 1)
    history.list_players.return_value = Page((HistoryPlayer(PEOPLE[0].id, PEOPLE[0].name, True, REV),), 0, 1)
    history.player.return_value = (CLUB, HistoryPlayer(PEOPLE[0].id, PEOPLE[0].name, True, REV))
    history.object_club.return_value = CLUB
    audit = AuditEntry(UUID(int=999), STAMP, "UTC", "Admin", "GAME_EDIT", "Game", GAME,
                       {"winner": "FASCIST", "secret": "never_render"}, {"winner": "LIBERAL"})
    history.list_audit.return_value = Page((audit,), 0, 1)
    history.audit.return_value = (CLUB, audit)
    events.event.return_value = event
    events.edit_setup.return_value = (event, EventSetup(GAME, EVENING, PEOPLE[:5], True), REV)
    events.update_event.return_value = replace(event, event_type=EventType.IQ_200, ruin_reason=None, self_admitted=False, comment=None)
    yield state, callback, answer, history, games, events, stats
    await storage.close()


async def start(ui, intent="ge", target=GAME, rev=REV):
    state, cb, _, h, _, e, _ = ui
    if intent in ("ge", "gv", "gr"):
        card = h.card.return_value
        h.card.return_value = replace(card, game=replace(card.game, status=GameStatus.VOID if intent == "gr" else GameStatus.ACTIVE),
                                      can_edit=intent != "gr")
    await handlers.begin(cb, HistoryMutation(entity_id=target, action=intent, revision=rev), state, h, e)


async def tap(ui, action, *, flow=None):
    state, cb, _, h, g, e, _ = ui
    await handlers.step(cb, HistoryStep(flow=flow or (await state.get_data())["flow"], action=action), state, h, g, e)


@pytest.mark.asyncio
async def test_edit_is_draft_back_cancel_and_only_final_save_calls_service(ui):
    state, cb, answer, h, games, _, _ = ui
    await start(ui)
    assert (await state.get_data())["participants"] == [str(p.id) for p in PEOPLE[:5]]
    await tap(ui, "participants")
    await handlers.pick(cb, HistoryPick(flow=(await state.get_data())["flow"], player_id=PEOPLE[4].id), state)
    await handlers.pick(cb, HistoryPick(flow=(await state.get_data())["flow"], player_id=PEOPLE[5].id), state)
    await tap(ui, "back")
    await tap(ui, "winner")
    await tap(ui, "win_L")
    await tap(ui, "v_LIBERAL_POLICIES")
    games.edit_game.assert_not_awaited()
    data = await state.get_data()
    saved_flow = data["flow"]
    await tap(ui, "save")
    kwargs = games.edit_game.await_args.kwargs
    assert kwargs["winner"] is Team.LIBERAL and kwargs["victory_type"] is VictoryType.LIBERAL_POLICIES
    assert PEOPLE[5].id in kwargs["participants"] and PEOPLE[4].id not in kwargs["participants"]
    assert kwargs["expected_revision"] == REV
    assert await state.get_state() is None
    await tap(ui, "save", flow=saved_flow)
    assert games.edit_game.await_count == 1 and answer.await_args.args[0] == ru.HISTORY_ERRORS["STALE"]
    await start(ui)
    await tap(ui, "cancel")
    assert await state.get_state() is None and games.edit_game.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("intent", ["gv", "gr", "ed", "pd", "pr"])
async def test_confirmation_calls_correct_service_once(ui, intent):
    state, _, answer, _, games, events, _ = ui
    target = EVENT if intent == "ed" else PEOPLE[0].id if intent in ("pd", "pr") else GAME
    await start(ui, intent, target)
    data = await state.get_data()
    assert ru.HISTORY_CONFIRM[intent] in answer.await_args.args[0]
    await tap(ui, "confirm")
    assert await state.get_state() is None
    if intent in ("gv", "gr"):
        games.set_game_void.assert_awaited_once_with(8802, GAME, expected_revision=REV, restore=intent == "gr")
    elif intent == "ed":
        events.delete_event.assert_awaited_once_with(8802, EVENT, expected_revision=REV)
    await tap(ui, "confirm", flow=data["flow"])
    assert answer.await_args.args[0] == ru.HISTORY_ERRORS["STALE"]


@pytest.mark.asyncio
@pytest.mark.parametrize("old_intent,new_intent,action", [("ge", "ge", "save"), ("gv", "gr", "confirm"),
    ("gr", "gv", "confirm"), ("ed", "ge", "confirm"), ("pd", "pr", "confirm"), ("pn", "ge", "cancel"),
    ("ge", "gv", "back"), ("ge", "ge", "cancel")])
async def test_old_flow_never_changes_new_entity_or_draft(ui, old_intent, new_intent, action):
    state, cb, answer, h, g, e, _ = ui
    await start(ui, old_intent, EVENT if old_intent == "ed" else PEOPLE[0].id if old_intent.startswith("p") else GAME)
    old = (await state.get_data())["flow"]
    await state.clear()
    await start(ui, new_intent, PEOPLE[0].id if new_intent.startswith("p") else GAME)
    before = await state.get_data()
    await tap(ui, action, flow=old)
    assert await state.get_data() == before
    assert answer.await_args.args[0] == ru.HISTORY_ERRORS["STALE"]
    g.edit_game.assert_not_awaited()
    g.set_game_void.assert_not_awaited()
    e.delete_event.assert_not_awaited()
    h.edit_player.assert_not_awaited()


@pytest.mark.asyncio
async def test_old_player_selection_cannot_toggle_new_flow(ui):
    state, cb, answer, _, _, _, _ = ui
    await start(ui)
    await tap(ui, "participants")
    old = (await state.get_data())["flow"]
    await tap(ui, "back")
    await tap(ui, "participants")
    before = await state.get_data()
    await handlers.pick(cb, HistoryPick(flow=old, player_id=PEOPLE[0].id), state)
    assert await state.get_data() == before and answer.await_args.args[0] == ru.HISTORY_ERRORS["STALE"]


@pytest.mark.asyncio
async def test_old_entry_button_is_rejected_while_draft_active_or_entity_changed(ui):
    state, _, answer, h, g, _, _ = ui
    await start(ui)
    before = await state.get_data()
    await start(ui, "gv")
    assert await state.get_data() == before
    await state.clear()
    await start(ui, rev="outdated-version")
    assert await state.get_state() is None
    assert answer.await_args.args[0] == ru.HISTORY_ERRORS["STALE"]
    g.set_game_void.assert_not_awaited()


@pytest.mark.asyncio
async def test_permission_error_and_revision_change_at_save_leave_draft_intact(ui):
    state, _, answer, _, g, _, _ = ui
    await start(ui)
    g.edit_game.side_effect = GameError(GameErrorCode.STALE)
    before = await state.get_data()
    await tap(ui, "save")
    assert await state.get_data() == before
    assert answer.await_args.args[0] == ru.HISTORY_ERRORS["STALE"]


@pytest.mark.asyncio
async def test_rename_accepts_only_reply_to_current_prompt_and_requires_confirmation(ui):
    state, cb, answer, h, _, _, _ = ui
    await start(ui, "pn", PEOPLE[0].id)
    prompt = (await state.get_data())["prompt_id"]
    message = cb.message.model_copy(update={"text": " New <b>name</b> ", "reply_to_message": cb.message.model_copy(update={"message_id": prompt - 1})})
    await handlers.rename(message, state)
    assert (await state.get_data())["stage"] == "rename"
    message = message.model_copy(update={"reply_to_message": cb.message.model_copy(update={"message_id": prompt})})
    await handlers.rename(message, state)
    assert (await state.get_data())["stage"] == "confirm"
    h.edit_player.assert_not_awaited()
    await tap(ui, "confirm")
    h.edit_player.assert_awaited_once_with(8802, PEOPLE[0].id, expected_revision=REV,
                                         display_name="New <b>name</b>", is_active=None)
    await handlers.rename(message, state)
    assert h.edit_player.await_count == 1
    assert answer.await_args.args[0] == ru.HISTORY_ERRORS["STALE"]


@pytest.mark.asyncio
async def test_event_edit_reuses_wizard_and_update_not_create(ui):
    state, cb, answer, _, games, events, _ = ui
    await start(ui, "ee", EVENT)
    assert await state.get_state() == EventFlow.active.state
    await choose(cb, EventChoice(flow=(await state.get_data())["flow"], kind="t", code="IQ_200"), state)
    await pick_player(cb, EventPick(flow=(await state.get_data())["flow"], player_id=PEOPLE[1].id), state)
    await event_action(cb, EventAction(flow=(await state.get_data())["flow"], action="skip"), state, games, events)
    old = (await state.get_data())["flow"]
    await event_save(cb, EventSave(flow=old, draft_id=EVENT), state, events)
    events.update_event.assert_awaited_once_with(8802, EVENT, player_id=PEOPLE[1].id, event_type=EventType.IQ_200,
        ruin_reason=None, self_admitted=False, comment="<b>comment</b>", expected_revision=REV)
    events.create_event.assert_not_awaited()
    await event_save(cb, EventSave(flow=old, draft_id=EVENT), state, events)
    assert events.update_event.await_count == 1
    await state.clear()
    await start(ui, "ee", EVENT)
    before = await state.get_data()
    await event_save(cb, EventSave(flow=old, draft_id=EVENT), state, events)
    assert await state.get_data() == before
    assert answer.await_args.args[0] == ru.EVENT_ERRORS["STALE"]


@pytest.mark.asyncio
@pytest.mark.parametrize("section", ["menu", "sessions", "recent", "players", "manage", "audit", "pgames", "player", "entry", "event", "game"])
async def test_history_screens_plain_text_and_never_cancel_new_wizard(ui, section):
    state, cb, answer, h, _, events, stats = ui
    await state.set_state("GameFlow:active")
    await state.set_data({"flow": "new-wizard"})
    await handlers.page(cb, HistoryPage(scope_id=CLUB, section=section), h, stats, events)
    assert answer.await_count >= 1
    assert all(c.kwargs.get("parse_mode", "missing") is None for c in answer.await_args_list)
    assert all(len(c.args[0]) <= 4096 for c in answer.await_args_list)
    assert await state.get_data() == {"flow": "new-wizard"}
    if section == "entry":
        assert "never_render" not in answer.await_args.args[0]


@pytest.mark.asyncio
async def test_mutation_controls_match_permissions_and_callback_sizes(ui):
    _, _, _, h, _, _, _ = ui
    card = h.card.return_value
    all_menus = [keyboards.card(card), keyboards.edit_menu("0123456789abcdef"), keyboards.confirm("0123456789abcdef")]
    readonly = keyboards.card(replace(card, can_edit=False, is_admin=False, can_add_event=False))
    assert all(not b.callback_data.startswith(("hm:", "estart:")) for row in readonly.inline_keyboard for b in row)
    void_menu = keyboards.card(replace(card, game=replace(card.game, status=GameStatus.VOID), can_edit=False, can_add_event=False))
    assert ru.HISTORY_RESTORE in {b.text for row in void_menu.inline_keyboard for b in row}
    all_menus += [void_menu, keyboards.picks(PEOPLE, {PEOPLE[0].id}, "0123456789abcdef"),
                  keyboards.player(HistoryPlayer(PEOPLE[0].id, PEOPLE[0].name, True, REV), CLUB)]
    for menu in all_menus:
        assert all(len(b.callback_data.encode()) <= 64 for row in menu.inline_keyboard for b in row)


@pytest.mark.asyncio
@pytest.mark.parametrize("intent", ["ge", "gv", "gr", "ee", "ed", "pn", "pd", "pr"])
async def test_each_old_entry_revision_is_rejected(ui, intent):
    state, _, answer, _, g, _, _ = ui
    target = EVENT if intent in ("ee", "ed") else PEOPLE[0].id if intent.startswith("p") else GAME
    await start(ui, intent, target, rev="old")
    assert await state.get_state() is None
    assert answer.await_args.args[0] == ru.HISTORY_ERRORS["STALE"]
    g.edit_game.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("intent", ["ge", "gv", "gr", "ed", "pd", "pr"])
async def test_real_dispatcher_serializes_duplicate_mutations(ui, intent):
    from app import main
    routers = [main.start_router, main.stats_router, main.history_router, main.membership_router, main.admin_router,
               main.sessions_router, main.games_router, main.events_router]
    for r in routers:
        r._parent_router = None
    dispatcher = create_dispatcher()
    bot = Bot(token="123456:abcdefghijklmnopqrstuvwxyzABCDEF")
    state = dispatcher.fsm.get_context(bot=bot, chat_id=8802, user_id=8802)
    _, cb, _, h, g, e, stats = ui
    actual_ui = (state, *ui[1:])
    target = EVENT if intent == "ed" else PEOPLE[0].id if intent.startswith("p") else GAME
    await start(actual_ui, intent, target)
    flow = (await state.get_data())["flow"]
    payload = HistoryStep(flow=flow, action="save" if intent == "ge" else "confirm").pack()
    async def slow(*args, **kwargs):
        await asyncio.sleep(.01)
    method = g.edit_game if intent == "ge" else g.set_game_void if intent in ("gv", "gr") else e.delete_event if intent == "ed" else h.edit_player
    method.side_effect = slow
    async def send(i):
        query = cb.model_copy(update={"id": str(i), "data": payload})
        return await dispatcher.feed_update(bot, Update(update_id=i, callback_query=query),
            history_service=h, game_service=g, event_service=e, stats_service=stats)
    try:
        await asyncio.gather(send(1), send(2))
        assert method.await_count == 1
        assert await state.get_state() is None
        # The production router handles existing GameCard callbacks before the older Event router.
        before = h.card.await_count
        await dispatcher.feed_update(bot, Update(update_id=3, callback_query=cb.model_copy(update={"data": GameCard(game_id=GAME).pack()})), history_service=h)
        assert h.card.await_count == before + 1
    finally:
        await dispatcher.fsm.close()
        await bot.session.close()
        for r in dispatcher.sub_routers:
            r._parent_router = None


@pytest.mark.asyncio
async def test_hitler_and_participant_changes_keep_roles_repairable(ui):
    state, cb, _, _, g, _, _ = ui
    await start(ui)
    await tap(ui, "hitler")
    await handlers.pick(cb, HistoryPick(flow=(await state.get_data())["flow"], player_id=PEOPLE[1].id), state)
    data = await state.get_data()
    assert data["hitler"] == str(PEOPLE[1].id) and data["fascists"] == []
    await tap(ui, "fascists")
    await handlers.pick(cb, HistoryPick(flow=(await state.get_data())["flow"], player_id=PEOPLE[0].id), state)
    await tap(ui, "back")
    await tap(ui, "save")
    assert g.edit_game.await_args.kwargs["hitler"] == PEOPLE[1].id
    assert g.edit_game.await_args.kwargs["fascists"] == (PEOPLE[0].id,)
    await start(ui)
    await tap(ui, "participants")
    for pid in (PEOPLE[0].id, PEOPLE[5].id):
        await handlers.pick(cb, HistoryPick(flow=(await state.get_data())["flow"], player_id=pid), state)
    await tap(ui, "back")
    assert (await state.get_data())["hitler"] is None
    await tap(ui, "save")
    assert g.edit_game.await_count == 1  # Must select a new Hitler before another Save.


@pytest.mark.asyncio
async def test_event_edit_can_clear_comment_and_old_clear_is_stale(ui):
    state, cb, answer, _, g, events, _ = ui
    await start(ui, "ee", EVENT)
    await choose(cb, EventChoice(flow=(await state.get_data())["flow"], kind="t", code="IQ_200"), state)
    await pick_player(cb, EventPick(flow=(await state.get_data())["flow"], player_id=PEOPLE[1].id), state)
    flow = (await state.get_data())["flow"]
    await event_action(cb, EventAction(flow=flow, action="clear"), state, g, events)
    assert (await state.get_data())["stage"] == "review" and (await state.get_data())["comment"] is None
    before = await state.get_data()
    await event_action(cb, EventAction(flow=flow, action="clear"), state, g, events)
    assert await state.get_data() == before and answer.await_args.args[0] == ru.EVENT_ERRORS["STALE"]
    await event_save(cb, EventSave(flow=before["flow"], draft_id=EVENT), state, events)
    assert events.update_event.await_args.kwargs["comment"] is None


@pytest.mark.asyncio
async def test_invalid_victory_and_unknown_back_are_human_errors(ui):
    state, _, answer, _, g, _, _ = ui
    await start(ui)
    await tap(ui, "victory")
    await tap(ui, "v_FASCIST_BOGUS")
    assert answer.await_args.args[0] == ru.HISTORY_ERRORS["INVALID_VICTORY"]
    await state.clear()
    await start(ui, "gv")
    before = await state.get_data()
    await tap(ui, "back")
    assert await state.get_data() == before and answer.await_args.args[0] == ru.HISTORY_ERRORS["STALE"]
    g.set_game_void.assert_not_awaited()
