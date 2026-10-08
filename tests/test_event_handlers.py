"""Event FSM and Telegram routing with real aiogram storage/isolation, mocked transport."""

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, ForceReply, InlineKeyboardMarkup, Message, Update, User

from app.bot.callbacks import EventAction, EventAfter, EventChoice, EventPick, EventSave, EventStart, GameAfter, GameCard
from app.bot.handlers.events import EventFlow, action, after, choose, comment, game_card, pick_player, save, start_event
from app.bot.handlers.games import after as game_after
from app.bot.keyboards import events as keyboards
from app.domain.enums import EventType, RuinReason, SessionStatus, Team, VictoryType
from app.locales import ru
from app.main import create_dispatcher
from app.services.events import EventSetup, EventView
from app.services.games import GamePerson, GameResult
from app.services.sessions import SessionView

PEOPLE = tuple(GamePerson(UUID(int=i), f"Player {i}") for i in range(1, 6))
GAME_ID, SESSION = UUID(int=100), UUID(int=101)
STALE = ru.EVENT_ERRORS["STALE"]


@pytest_asyncio.fixture
async def flow(monkeypatch):
    storage = MemoryStorage()
    state = FSMContext(storage, StorageKey(bot_id=1, chat_id=200, user_id=200))
    message = Message(message_id=1, date=datetime.now(timezone.utc), chat=Chat(id=200, type="private"),
                      from_user=User(id=200, is_bot=False, first_name="Stat"))
    callback = CallbackQuery(id="test", from_user=message.from_user, chat_instance="test", message=message)
    serial = 10

    async def send(*args, **kwargs):
        nonlocal serial
        serial += 1
        return message.model_copy(update={"message_id": serial})

    answer = AsyncMock(side_effect=send)
    monkeypatch.setattr(Message, "answer", answer)
    monkeypatch.setattr(CallbackQuery, "answer", AsyncMock())
    events, games, sessions = AsyncMock(), AsyncMock(), AsyncMock()
    events.setup.return_value = EventSetup(GAME_ID, SESSION, PEOPLE, True)
    games.game.return_value = GameResult(GAME_ID, SESSION, 1, PEOPLE, PEOPLE[0], PEOPLE[1:2], PEOPLE[2:],
                                        Team.LIBERAL, VictoryType.HITLER_KILLED)
    sessions.view.return_value = SessionView(SESSION, UUID(int=102), "Club", SessionStatus.ACTIVE,
                                            "Stat", PEOPLE, 1, 1, 0, True)
    saved = None

    async def create(tid, gid, draft_id, pid, event_type, reason=None, admitted=False, text=None):
        nonlocal saved
        saved = EventView(draft_id, gid, pid, PEOPLE[0].name, event_type, reason, admitted, text)
        await asyncio.sleep(0)
        return saved, True

    async def get_event(*args):
        return saved

    events.create_event.side_effect = create
    events.event.side_effect = get_event
    yield state, callback, answer, events, games, sessions
    await storage.close()


def rendered(answer, label, kind):
    for call in reversed(answer.await_args_list):
        markup = call.kwargs.get("reply_markup")
        if isinstance(markup, InlineKeyboardMarkup):
            for row in markup.inline_keyboard:
                for b in row:
                    if b.text == label:
                        return kind.unpack(b.callback_data)
    raise AssertionError(f"Button not rendered: {label}")


async def draft(state, stage, kind=EventType.RUIN):
    data = {"stage": stage, "flow": uuid4().hex[:16], "draft_id": str(uuid4()),
            "game_id": str(GAME_ID), "session_id": str(SESSION),
            "participants": [(str(p.id), p.name) for p in PEOPLE], "event_type": kind.value,
            "player_id": str(PEOPLE[0].id), "ruin_reason": RuinReason.OTHER.value if kind is EventType.RUIN else None,
            "self_admitted": False, "comment": None, "comment_prompt_id": 123}
    await state.set_state(EventFlow.active)
    await state.set_data(data)
    return data


async def invoke(flow, payload):
    state, callback, _, events, games, sessions = flow
    if isinstance(payload, EventChoice):
        await choose(callback, payload, state)
    elif isinstance(payload, EventPick):
        await pick_player(callback, payload, state)
    elif isinstance(payload, EventAction):
        await action(callback, payload, state, games, events)
    elif isinstance(payload, EventSave):
        await save(callback, payload, state, events)
    elif isinstance(payload, EventAfter):
        await after(callback, payload, state, events, games, sessions)
    else:
        await start_event(callback, payload, state, events)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(EventType))
async def test_happy_path_all_types(flow, kind):
    state, callback, answer, events, _, _ = flow
    await start_event(callback, EventStart(game_id=GAME_ID), state, events)
    await invoke(flow, rendered(answer, ru.EVENT_TYPE_LABELS[kind.value], EventChoice))
    await invoke(flow, rendered(answer, PEOPLE[0].name, EventPick))
    if kind is EventType.RUIN:
        await invoke(flow, rendered(answer, ru.RUIN_REASON_LABELS["WRONG_VOTE"], EventChoice))
        await invoke(flow, rendered(answer, ru.EVENT_YES, EventChoice))
    assert (await state.get_data())["stage"] == "comment"
    assert isinstance(answer.await_args.kwargs["reply_markup"], ForceReply)
    await invoke(flow, rendered(answer, ru.EVENT_SKIP, EventAction))
    assert ru.EVENT_REVIEW in answer.await_args.args[0]
    events.create_event.assert_not_awaited()
    save_button = rendered(answer, ru.EVENT_SAVE, EventSave)
    await invoke(flow, save_button)
    assert await state.get_state() is None
    assert ru.EVENT_SAVED in answer.await_args.args[0]
    assert answer.await_args.kwargs["parse_mode"] is None
    assert events.create_event.await_args.args[4] is kind
    assert events.create_event.await_args.args[6] is (kind is EventType.RUIN)
    assert rendered(answer, ru.EVENT_MORE, EventAfter).action == "m"
    assert rendered(answer, ru.EVENT_GAME, EventAfter).action == "g"
    assert rendered(answer, ru.EVENT_HOME, EventAfter).action == "h"
    await invoke(flow, save_button)
    events.create_event.assert_awaited_once()
    events.event.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage,code", [
    ("type", "type"), ("player", "player"), ("reason", "reason"), ("admitted", "admitted"),
    ("comment", "skip"), ("review", "save"),
    *[(stage, "cancel") for stage in ("type", "player", "reason", "admitted", "comment", "review")],
    *[(stage, "back") for stage in ("player", "reason", "admitted", "comment", "review")],
])
async def test_stale_event_buttons_never_change_new_draft(flow, stage, code):
    state, _, answer, events, games, sessions = flow
    old = await draft(state, stage)
    if code in ("type", "reason", "admitted"):
        payload = EventChoice(flow=old["flow"], kind={"type": "t", "reason": "r", "admitted": "a"}[code],
                              code={"type": "RUIN", "reason": "OTHER", "admitted": "y"}[code])
    elif code == "player":
        payload = EventPick(flow=old["flow"], player_id=PEOPLE[0].id)
    elif code == "save":
        payload = EventSave(flow=old["flow"], draft_id=UUID(old["draft_id"]))
    else:
        payload = EventAction(flow=old["flow"], action=code)
    before = await draft(state, stage)
    await invoke(flow, payload)
    assert await state.get_data() == before and await state.get_state() == EventFlow.active.state
    assert events.mock_calls == games.mock_calls == sessions.mock_calls == []
    assert answer.await_args.args[0] == STALE


@pytest.mark.asyncio
@pytest.mark.parametrize("stage,target", [("player", "type"), ("reason", "player"),
                                          ("admitted", "reason"), ("comment", "admitted"), ("review", "comment")])
async def test_back_remains_in_same_event(flow, stage, target):
    state, _, answer, _, _, _ = flow
    old = await draft(state, stage)
    payload = EventAction(flow=old["flow"], action="back")
    await invoke(flow, payload)
    before = await state.get_data()
    assert before["stage"] == target and before["draft_id"] == old["draft_id"]
    assert before["flow"] != old["flow"]
    await invoke(flow, payload)
    assert await state.get_data() == before and answer.await_args.args[0] == STALE


@pytest.mark.asyncio
async def test_cancel_returns_game_and_clears_draft(flow):
    state, _, answer, events, _, _ = flow
    old = await draft(state, "review")
    await invoke(flow, EventAction(flow=old["flow"], action="cancel"))
    assert await state.get_state() is None and await state.get_data() == {}
    events.create_event.assert_not_awaited()
    assert answer.await_args.args[0] == ru.EVENT_CANCELLED
    assert rendered(answer, ru.EVENT_ADD, EventStart).game_id == GAME_ID


@pytest.mark.asyncio
async def test_comment_is_bound_to_current_prompt_and_safe_plain_text(flow):
    state, callback, answer, _, _, _ = flow
    await draft(state, "comment", EventType.IQ_200)
    prompt = callback.message.model_copy(update={"message_id": 123})
    text = '<b>Name</b> & [link](https://example.com)'
    message = callback.message.model_copy(update={"text": text, "reply_to_message": prompt})
    # Reply to the old prompt cannot enter the new comment draft.
    current = await draft(state, "comment", EventType.IQ_200)
    current["comment_prompt_id"] = 124
    await state.set_data(current)
    await comment(message, state)
    assert await state.get_data() == current
    assert answer.await_args.args[0] == ru.EVENT_COMMENT_STALE
    prompt = prompt.model_copy(update={"message_id": 124})
    await comment(message.model_copy(update={"reply_to_message": prompt}), state)
    assert (await state.get_data())["comment"] == text
    assert (await state.get_data())["stage"] == "review"
    assert answer.await_args.kwargs["parse_mode"] is None
    assert text in answer.await_args.args[0]
    before = await state.get_data()
    await comment(message, state)
    assert await state.get_data() == before
    await state.clear()
    await comment(message, state)
    assert await state.get_data() == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["", " \n ", "x" * 501])
async def test_invalid_comment_keeps_prompt_and_state(flow, text):
    state, callback, answer, _, _, _ = flow
    before = await draft(state, "comment")
    message = callback.message.model_copy(update={"text": text,
        "reply_to_message": callback.message.model_copy(update={"message_id": 123})})
    await comment(message, state)
    assert await state.get_data() == before
    assert answer.await_args.args[0] == ru.EVENT_COMMENT_INVALID


@pytest.mark.asyncio
async def test_event_entry_and_post_save_buttons_do_not_replace_another_flow(flow):
    state, callback, answer, events, games, sessions = flow
    old = await draft(state, "review")
    await invoke(flow, EventSave(flow=old["flow"], draft_id=UUID(old["draft_id"])))
    buttons = [rendered(answer, label, EventAfter) for label in (ru.EVENT_MORE, ru.EVENT_GAME, ru.EVENT_HOME)]
    current = await draft(state, "type")
    events.reset_mock()
    await start_event(callback, EventStart(game_id=GAME_ID), state, events)
    await game_card(callback, GameCard(game_id=GAME_ID), state, games, events)
    for payload in buttons:
        await invoke(flow, payload)
        assert answer.await_args.args[0] == STALE
    assert await state.get_data() == current
    assert events.mock_calls == games.mock_calls == sessions.mock_calls == []


@pytest.mark.asyncio
async def test_game_saved_entry_and_add_more(flow):
    state, callback, answer, events, games, sessions = flow
    await state.set_data({"stage": "saved", "flow": "x" * 16, "game_id": str(GAME_ID)})
    await game_after(callback, GameAfter(flow="x" * 16, game_id=GAME_ID, action="m"), state, games, sessions, events)
    assert (await state.get_data())["stage"] == "type"
    await state.clear()
    old = await draft(state, "review")
    await invoke(flow, EventSave(flow=old["flow"], draft_id=UUID(old["draft_id"])))
    more = rendered(answer, ru.EVENT_MORE, EventAfter)
    await invoke(flow, more)
    assert (await state.get_data())["draft_id"] != old["draft_id"]
    assert (await state.get_data())["stage"] == "type"


@pytest.mark.asyncio
@pytest.mark.parametrize("can_create", [True, False])
async def test_game_card_supports_unknown_victory_and_checks_permissions(flow, can_create):
    state, callback, answer, events, games, _ = flow
    games.game.return_value = replace(games.game.return_value, victory_type=VictoryType.UNKNOWN)
    events.setup.return_value = replace(events.setup.return_value, can_create=can_create)
    await game_card(callback, GameCard(game_id=GAME_ID), state, games, events)
    assert ru.GAME_VICTORY_UNKNOWN in answer.await_args.args[0]
    labels = [b.text for row in answer.await_args.kwargs["reply_markup"].inline_keyboard for b in row]
    assert (ru.EVENT_ADD in labels) is can_create
    if not can_create:
        await start_event(callback, EventStart(game_id=GAME_ID), state, events)
        assert await state.get_state() is None and await state.get_data() == {}
        assert answer.await_args.args[0] == ru.EVENT_ERRORS["NO_ACCESS"]


def test_event_keyboards_fit_telegram_limit():
    token = "x" * 16
    markups = [keyboards.choices(ru.EVENT_TYPE_LABELS, "t", token), keyboards.players(PEOPLE, token),
               keyboards.choices(ru.RUIN_REASON_LABELS, "r", token), keyboards.comment(token),
               keyboards.review(uuid4(), token), keyboards.saved(uuid4(), token),
               keyboards.game_card(GAME_ID, SESSION, True)]
    for markup in markups:
        for row in markup.inline_keyboard:
            for b in row:
                assert len(b.callback_data.encode()) <= 64


@pytest.mark.asyncio
async def test_dispatcher_serializes_save_and_text_is_ignored_after_completion(flow):
    _, callback, answer, events, games, sessions = flow
    bot = Bot(token="123456:example", default=DefaultBotProperties(parse_mode="HTML"))
    dispatcher = create_dispatcher()
    state = dispatcher.fsm.get_context(bot=bot, chat_id=200, user_id=200)
    try:
        async def dispatch(payload, uid):
            raw = payload if isinstance(payload, str) else payload.pack()
            query = callback.model_copy(update={"data": raw, "id": str(uid)})
            await dispatcher.feed_update(bot, Update(update_id=uid, callback_query=query),
                event_service=events, game_service=games, session_service=sessions)

        current = await draft(state, "review")
        for index, raw in enumerate(["ec:bad", "ep:bad", "ea:bad", "es:bad", "ef:bad", "estart:bad", "gcard:bad"]):
            await dispatch(raw, index)
            assert await state.get_data() == current and answer.await_args.args[0] == STALE
        payload = EventSave(flow=current["flow"], draft_id=UUID(current["draft_id"]))
        await asyncio.gather(dispatch(payload, 20), dispatch(payload, 21))
        events.create_event.assert_awaited_once()
        events.event.assert_awaited_once()
        assert await state.get_state() is None
        receipt = await state.get_data()
        answer.reset_mock()
        message = callback.message.model_copy(update={"text": "old comment", "reply_to_message": callback.message})
        await dispatcher.feed_update(bot, Update(update_id=30, message=message),
                event_service=events, game_service=games, session_service=sessions)
        assert await state.get_data() == receipt
        answer.assert_not_awaited()
        # A new draft rejects the just-saved callback before even reading an Event.
        before = await draft(state, "review")
        events.reset_mock()
        await dispatch(payload, 31)
        assert await state.get_data() == before and events.mock_calls == []
        assert answer.await_args.args[0] == STALE
    finally:
        await dispatcher.fsm.close()
        await bot.session.close()
        for router in dispatcher.sub_routers:
            router._parent_router = None
