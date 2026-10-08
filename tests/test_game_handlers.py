"""Game screen ownership, navigation and serialized callbacks without Telegram I/O."""

import asyncio
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

from app.bot.callbacks import GameAction, GameAfter, GameChoice, GamePick, GameSave
from app.bot.handlers.games import GameFlow, _screen, action, after, begin_wizard, choice, pick, save
from app.bot.keyboards import games as keyboards
from app.domain.enums import SessionStatus, Team, VictoryType
from app.locales import ru
from app.main import create_dispatcher
from app.services.games import GameError, GameErrorCode, GamePerson, GameResult, GameSetup
from app.services.sessions import SessionView

PLAYERS = tuple(GamePerson(UUID(int=i), f"Player {i}") for i in range(1, 13))
SESSION = UUID(int=100)
GAME = GameResult(UUID(int=200), SESSION, 1, PLAYERS[:8], PLAYERS[0], PLAYERS[1:3],
                  PLAYERS[3:8], Team.LIBERAL, VictoryType.HITLER_KILLED)
STALE = ru.GAME_ERRORS["STALE"]


@pytest_asyncio.fixture
async def flow(monkeypatch):
    storage = MemoryStorage()
    state = FSMContext(storage, StorageKey(bot_id=1, chat_id=200, user_id=200))
    answer = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer)
    monkeypatch.setattr(CallbackQuery, "answer", AsyncMock())
    message = Message(message_id=1, date=datetime.now(timezone.utc), chat=Chat(id=200, type="private"),
                      from_user=User(id=200, is_bot=False, first_name="Statistician"))
    callback = CallbackQuery(id="test", from_user=message.from_user, chat_instance="test", message=message)
    games, sessions = AsyncMock(), AsyncMock()
    games.setup.return_value = GameSetup(SESSION, 1, PLAYERS[:8], PLAYERS)
    games.create_game.return_value = (GAME, True)
    games.saved_draft.return_value = games.game.return_value = GAME
    sessions.view.return_value = SessionView(SESSION, UUID(int=101), "Club", SessionStatus.ACTIVE,
                                             "Stat", PLAYERS[:8], 1, 1, 0, True)
    yield state, callback, answer, games, sessions
    await storage.close()


async def draft(state, stage):
    await state.set_state(GameFlow.active)
    return await _screen(state, {
        "session_id": str(SESSION), "draft_id": str(uuid4()), "sequence": 1,
        "stage": stage, "page": 0, "candidates": [(str(p.id), p.name) for p in PLAYERS],
        "participants": [str(p.id) for p in PLAYERS[:8]], "hitler": str(PLAYERS[0].id),
        "fascists": [str(p.id) for p in PLAYERS[1:3]],
        "winner": Team.LIBERAL.value, "victory": VictoryType.HITLER_KILLED.value,
    })


async def invoke(flow, payload):
    state, callback, _, games, sessions = flow
    if isinstance(payload, GamePick):
        await pick(callback, payload, state)
    elif isinstance(payload, GameChoice):
        await choice(callback, payload, state)
    elif isinstance(payload, GameAction):
        await action(callback, payload, state, sessions)
    elif isinstance(payload, GameSave):
        await save(callback, payload, state, games)
    else:
        await after(callback, payload, state, games, sessions)


def rendered(answer, label, callback_type):
    keyboard = answer.await_args.kwargs["reply_markup"]
    raw = next(b.callback_data for row in keyboard.inline_keyboard for b in row if b.text == label)
    return callback_type.unpack(raw)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage,kind,code", [
    ("participants", "pick", "p"), ("hitler", "pick", "h"), ("fascists", "pick", "f"),
    ("winner", "choice", "winner"), ("victory", "choice", "victory"),
    ("participants", "action", "participants_done"), ("participants", "action", "next"),
    ("participants", "action", "prev"), ("hitler", "action", "participants"),
    ("fascists", "action", "fascists_done"), ("fascists", "action", "hitler_back"),
    ("winner", "action", "fascists_back"), ("victory", "action", "winner_back"),
    ("review", "action", "victory_back"), ("review", "action", "cancel"),
    ("participants", "action", "cancel"), ("hitler", "action", "cancel"),
    ("fascists", "action", "cancel"), ("winner", "action", "cancel"),
    ("victory", "action", "cancel"), ("review", "save", "save"),
])
async def test_old_button_cannot_mutate_new_draft(flow, stage, kind, code):
    state, _, answer, games, sessions = flow
    old = await draft(state, stage)
    if kind == "pick":
        payload = GamePick(flow=old["flow"], step=code, player_id=PLAYERS[1].id)
    elif kind == "choice":
        payload = GameChoice(flow=old["flow"], kind=code, code="L" if code == "winner" else "HK")
    elif kind == "save":
        payload = GameSave(flow=old["flow"], draft_id=UUID(old["draft_id"]))
    else:
        payload = GameAction(flow=old["flow"], action=code)
    before = await draft(state, stage)
    await invoke(flow, payload)
    assert await state.get_data() == before
    assert await state.get_state() == GameFlow.active.state
    assert games.mock_calls == sessions.mock_calls == []
    assert answer.await_args.args[0] == STALE


@pytest.mark.asyncio
@pytest.mark.parametrize("action_code", ["h", "m", "n", "s", "c"])
async def test_old_post_save_controls_cannot_replace_current_wizard(flow, action_code):
    state, _, answer, games, sessions = flow
    before = await draft(state, "hitler")
    await invoke(flow, GameAfter(flow=before["flow"], game_id=GAME.id, action=action_code))
    assert await state.get_data() == before
    assert games.mock_calls == sessions.mock_calls == []
    assert answer.await_args.args[0] == STALE


@pytest.mark.asyncio
@pytest.mark.parametrize("stage,action_code,target", [
    ("fascists", "hitler_back", "hitler"), ("winner", "fascists_back", "fascists"),
    ("victory", "winner_back", "winner"), ("review", "victory_back", "victory"),
    ("hitler", "participants", "participants"),
])
async def test_back_stays_in_draft_and_cannot_be_replayed_after_revisiting(flow, stage, action_code, target):
    state, _, answer, _, _ = flow
    before = await draft(state, stage)
    payload = GameAction(flow=before["flow"], action=action_code)
    await invoke(flow, payload)
    current = await state.get_data()
    assert current["stage"] == target and current["draft_id"] == before["draft_id"]
    assert current["flow"] != before["flow"]
    # Return to the original stage within the SAME draft: its old buttons are still stale.
    current = await _screen(state, current, stage=stage)
    await invoke(flow, payload)
    assert await state.get_data() == current
    assert answer.await_args.args[0] == STALE


@pytest.mark.asyncio
async def test_current_cancel_clears_fsm_and_returns_session(flow):
    state, _, answer, games, sessions = flow
    current = await draft(state, "review")
    await invoke(flow, GameAction(flow=current["flow"], action="cancel"))
    assert await state.get_state() is None and await state.get_data() == {}
    sessions.view.assert_awaited_once_with(200, SESSION)
    games.create_game.assert_not_awaited()
    assert "Запись партии отменена" in answer.await_args.args[0]
    assert "Club" in answer.await_args.args[0]
    assert "Записать игру" in str(answer.await_args.kwargs["reply_markup"])


@pytest.mark.asyncio
async def test_save_receipt_is_idempotent_and_cannot_affect_new_draft(flow):
    state, _, answer, games, _ = flow
    data = await draft(state, "review")
    payload = GameSave(flow=data["flow"], draft_id=UUID(data["draft_id"]))
    await invoke(flow, payload)
    receipt = await state.get_data()
    assert await state.get_state() is None
    await invoke(flow, payload)
    assert await state.get_data() == receipt
    games.create_game.assert_awaited_once()
    games.saved_draft.assert_awaited_once_with(200, payload.draft_id)
    before = await draft(state, "review")
    games.reset_mock()
    await invoke(flow, payload)
    assert await state.get_data() == before
    assert games.mock_calls == []
    assert answer.await_args.args[0] == STALE


@pytest.mark.asyncio
async def test_save_from_prior_review_visit_is_stale_even_for_same_draft(flow):
    state, _, answer, games, _ = flow
    old = await draft(state, "review")
    payload = GameSave(flow=old["flow"], draft_id=UUID(old["draft_id"]))
    await invoke(flow, GameAction(flow=old["flow"], action="victory_back"))
    current = await state.get_data()
    await invoke(flow, GameChoice(flow=current["flow"], kind="victory", code="HK"))
    before = await state.get_data()
    await invoke(flow, payload)
    assert await state.get_data() == before
    assert games.mock_calls == []
    assert answer.await_args.args[0] == STALE


@pytest.mark.asyncio
async def test_saved_next_same_keeps_ux_and_invalidates_previous_controls(flow):
    state, _, answer, _, _ = flow
    data = await draft(state, "review")
    await invoke(flow, GameSave(flow=data["flow"], draft_id=UUID(data["draft_id"])))
    old_next = rendered(answer, "🎲 Следующая игра", GameAfter)
    assert rendered(answer, "✨ Добавить момент", GameAfter).action == "m"
    assert rendered(answer, "🏠 К вечеру", GameAfter).action == "h"
    await invoke(flow, old_next)
    assert answer.await_args.args[0] == "Состав следующей партии тот же?"
    same = rendered(answer, "✅ Да", GameAfter)
    assert rendered(answer, "👥 Изменился", GameAfter).action == "c"
    before = await state.get_data()
    await invoke(flow, old_next)
    assert await state.get_data() == before and answer.await_args.args[0] == STALE
    await invoke(flow, same)
    current = await state.get_data()
    assert current["stage"] == "hitler" and current["draft_id"] != data["draft_id"]
    assert set(current["participants"]) == {str(p.id) for p in GAME.participants}
    await invoke(flow, same)
    assert await state.get_data() == current and answer.await_args.args[0] == STALE


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["", "неверный"])
async def test_invalid_tokens_and_lost_fsm_are_safe(flow, token):
    state, _, answer, _, _ = flow
    before = await draft(state, "hitler")
    await invoke(flow, GamePick(flow=token, step="h", player_id=PLAYERS[0].id))
    assert await state.get_data() == before and answer.await_args.args[0] == STALE
    await state.clear()
    await invoke(flow, GamePick(flow=before["flow"], step="h", player_id=PLAYERS[0].id))
    assert await state.get_data() == {} and answer.await_args.args[0] == STALE


def test_all_game_keyboards_fit_telegram_callback_limit():
    token = "x" * 16
    markups = [keyboards.participants(PLAYERS, set(), page, token) for page in (0, 1)]
    markups += [keyboards.hitler(PLAYERS[:10], token), keyboards.fascists(PLAYERS[1:10], {PLAYERS[1].id}, 1, token),
                keyboards.winner(token), keyboards.victory(Team.LIBERAL, token),
                keyboards.victory(Team.FASCIST, token), keyboards.review(uuid4(), token),
                keyboards.saved(GAME, token), keyboards.next_roster(GAME, token)]
    for markup in markups:
        for row in markup.inline_keyboard:
            for button in row:
                assert len(button.callback_data.encode()) <= 64


@pytest.mark.asyncio
async def test_dispatcher_serializes_game_callbacks_and_rejects_legacy_payloads(flow):
    _, callback, answer, games, sessions = flow
    bot = Bot(token="123456:example")
    dispatcher = create_dispatcher()
    state = dispatcher.fsm.get_context(bot=bot, chat_id=200, user_id=200)
    try:
        async def dispatch(payload, update_id):
            data = payload if isinstance(payload, str) else payload.pack()
            query = callback.model_copy(update={"data": data, "id": str(update_id)})
            await dispatcher.feed_update(bot, Update(update_id=update_id, callback_query=query),
                                         game_service=games, session_service=sessions)

        current = await draft(state, "review")
        legacy = ["gp:hitler:" + str(PLAYERS[0].id), "ga:cancel", "gc:winner:L",
                  "gs:" + current["draft_id"], "gafter:" + str(GAME.id) + ":same",
                  "gp2:bad", "ga2:bad", "gc2:bad", "gs2:bad", "gf2:bad"]
        for index, payload in enumerate(legacy):
            await dispatch(payload, index)
            assert await state.get_data() == current
            assert answer.await_args.args[0] == STALE
        games.create_game.assert_not_awaited()

        # Same-screen duplicate toggles have one effect, even when dispatched together.
        current = await draft(state, "fascists")
        current = await _screen(state, current, fascists=[])
        toggle = GamePick(flow=current["flow"], step="f", player_id=PLAYERS[1].id)
        await asyncio.gather(dispatch(toggle, 20), dispatch(toggle, 21))
        assert (await state.get_data())["fascists"] == [str(PLAYERS[1].id)]
        assert answer.await_args.args[0] == STALE

        for scenario in ("winner_twice", "back_winner", "cancel_winner", "save_twice"):
            current = await draft(state, "review" if scenario == "save_twice" else "winner")
            winner = GameChoice(flow=current["flow"], kind="winner", code="L")
            if scenario == "winner_twice":
                first = second = winner
            elif scenario == "back_winner":
                first, second = GameAction(flow=current["flow"], action="fascists_back"), winner
            elif scenario == "cancel_winner":
                first, second = GameAction(flow=current["flow"], action="cancel"), winner
            else:
                first = second = GameSave(flow=current["flow"], draft_id=UUID(current["draft_id"]))
            await asyncio.gather(dispatch(first, 30), dispatch(second, 31))
            result = await state.get_data()
            if scenario == "winner_twice":
                assert result["stage"] == "victory" and result["winner"] == Team.LIBERAL.value
            elif scenario == "back_winner":
                assert result["stage"] in ("fascists", "victory")
                expected = None if result["stage"] == "fascists" else Team.LIBERAL.value
                assert result["winner"] == expected
            elif scenario == "cancel_winner":
                assert result == {} or result["stage"] == "victory"
            else:
                assert result["stage"] == "saved" and await state.get_state() is None
                games.create_game.assert_awaited_once()
                games.saved_draft.assert_awaited_once()
                continue
            assert answer.await_args.args[0] == STALE
    finally:
        await dispatcher.fsm.close()
        await bot.session.close()
        # Production routers are module singletons; allow another factory test to attach them.
        for router in dispatcher.sub_routers:
            router._parent_router = None
