"""Session screen ownership and recovery UI without Telegram/network or PostgreSQL."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
import pytest_asyncio
from aiogram.filters import CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message, User

from app.bot.callbacks import RecoveryAction, SessionAction, SessionPick, SessionStart, SessionStep
from app.bot.handlers.sessions import (
    EveningFlow, _screen, evening_action, legacy_session_callback,
    pick_player, recovery_action, start_evening, step,
)
from app.bot.handlers.start import start
from app.bot.keyboards import sessions as keyboards
from app.domain.enums import RecoveryStatus, SessionStatus
from app.locales import ru
from app.services.sessions import RecoveryView, RosterPlayer, SessionView


@pytest_asyncio.fixture
async def flow(monkeypatch):
    storage = MemoryStorage()
    state = FSMContext(storage, StorageKey(bot_id=1, chat_id=200, user_id=200))
    answer = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer)
    monkeypatch.setattr(CallbackQuery, "answer", AsyncMock())
    message = Message(message_id=1, date=datetime.now(timezone.utc), chat=Chat(id=200, type="private"),
                      from_user=User(id=200, is_bot=False, first_name="Max"))
    callback = CallbackQuery(id="test", from_user=message.from_user, chat_instance="test", message=message)
    service = AsyncMock()
    service.view.return_value = SessionView(UUID(int=1), UUID(int=2), "Club", SessionStatus.ACTIVE,
                                           "Stat", (), 0, 0, 0, True, UUID(int=3), True)
    service.roster_choices.return_value = (RosterPlayer(UUID(int=4), "Next"),)
    service.recovery_choices.return_value = service.roster_choices.return_value
    yield state, callback, answer, service
    await storage.close()


def button(keyboard, text):
    return next(b.callback_data for row in keyboard.inline_keyboard for b in row if b.text == text)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["finish", "transfer", "recovery", "cancel"])
async def test_old_confirmation_cannot_change_or_cancel_new_intent(flow, action):
    state, callback, answer, service = flow
    old = await _screen(state, EveningFlow.confirming, session_id=str(UUID(int=1)), mode=action)
    old_button = SessionStep(flow=old, action=action)
    await _screen(state, EveningFlow.confirming, session_id=str(UUID(int=99)), mode=action)
    before = await state.get_data()
    await step(callback, old_button, state, service)
    assert await state.get_data() == before
    assert await state.get_state() == EveningFlow.confirming.state
    service.finish.assert_not_awaited()
    service.transfer.assert_not_awaited()
    service.request_recovery.assert_not_awaited()
    assert answer.await_args.args[0] == ru.SESSION_STALE


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["add", "remove", "transfer", "recovery", "create"])
async def test_old_player_selection_cannot_change_new_flow(flow, mode):
    state, callback, answer, service = flow
    stage = EveningFlow.selecting if mode == "create" else EveningFlow.choosing
    old = await _screen(state, stage, mode=mode, session_id=str(UUID(int=1)))
    await _screen(state, stage, mode=mode, session_id=str(UUID(int=99)))
    before = await state.get_data()
    await pick_player(callback, SessionPick(flow=old, player_id=UUID(int=4)), state, service)
    assert await state.get_data() == before
    assert service.mock_calls == []
    assert answer.await_args.args[0] == ru.SESSION_STALE


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["view", "roster", "add", "remove", "transfer", "finish", "record", "help"])
async def test_old_entry_button_does_not_replace_current_flow(flow, action):
    state, callback, answer, service = flow
    await _screen(state, EveningFlow.choosing, mode="add", session_id=str(UUID(int=99)))
    before = await state.get_data()
    games = AsyncMock()
    await evening_action(callback, SessionAction(session_id=UUID(int=1), action=action), state, service, games)
    assert await state.get_data() == before
    assert service.mock_calls == games.mock_calls == []
    assert answer.await_args.args[0] == ru.SESSION_STALE


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["create", "cancel", "next", "prev", "add", "remove", "back"])
async def test_stale_navigation_and_roster_menu_are_safe(flow, action):
    state, callback, answer, service = flow
    old = await _screen(state, EveningFlow.menu, mode="roster")
    await _screen(state, EveningFlow.menu, mode="roster")
    before = await state.get_data()
    await step(callback, SessionStep(flow=old, action=action), state, service)
    assert await state.get_data() == before
    assert service.mock_calls == []
    assert answer.await_args.args[0] == ru.SESSION_STALE


@pytest.mark.asyncio
async def test_finish_uses_bound_session_and_second_tap_is_stale(flow):
    state, callback, answer, service = flow
    await evening_action(callback, SessionAction(session_id=UUID(int=1), action="finish"),
                         state, service, AsyncMock())
    confirm = SessionStep.unpack(button(answer.await_args.kwargs["reply_markup"], ru.CONFIRM_BUTTON))
    service.finish.return_value = service.view.return_value
    await step(callback, confirm, state, service)
    service.finish.assert_awaited_once_with(200, UUID(int=1), expected_statistician_id=UUID(int=3))
    assert await state.get_state() is None
    await step(callback, confirm, state, service)
    service.finish.assert_awaited_once()
    assert answer.await_args.args[0] == ru.SESSION_STALE


@pytest.mark.asyncio
async def test_roster_selection_and_transfer_confirmation_rotate_tokens(flow):
    state, callback, answer, service = flow
    await evening_action(callback, SessionAction(session_id=UUID(int=1), action="transfer"),
                         state, service, AsyncMock())
    selection = SessionPick.unpack(button(answer.await_args.kwargs["reply_markup"], "Next"))
    await pick_player(callback, selection, state, service)
    confirmation = SessionStep.unpack(button(answer.await_args.kwargs["reply_markup"], ru.CONFIRM_BUTTON))
    assert selection.flow != confirmation.flow
    before = await state.get_data()
    await pick_player(callback, selection, state, service)
    assert await state.get_data() == before
    service.transfer.return_value = service.view.return_value
    await step(callback, confirmation, state, service)
    service.transfer.assert_awaited_once_with(200, UUID(int=1), UUID(int=4),
                                            expected_statistician_id=UUID(int=3))


@pytest.mark.asyncio
async def test_roster_add_uses_current_player_and_expected_statistician(flow):
    state, callback, answer, service = flow
    await evening_action(callback, SessionAction(session_id=UUID(int=1), action="roster"),
                         state, service, AsyncMock())
    choose = SessionStep.unpack(button(answer.await_args.kwargs["reply_markup"], ru.ARRIVED_BUTTON))
    await step(callback, choose, state, service)
    pick = SessionPick.unpack(button(answer.await_args.kwargs["reply_markup"], "Next"))
    service.add_player.return_value = service.view.return_value
    await pick_player(callback, pick, state, service)
    service.add_player.assert_awaited_once_with(200, UUID(int=1), UUID(int=4),
                                              expected_statistician_id=UUID(int=3))
    assert await state.get_state() is None


@pytest.mark.asyncio
async def test_start_selection_rotates_token_and_old_cancel_is_rejected(flow):
    state, callback, answer, service = flow
    service.active_players.return_value = (RosterPlayer(UUID(int=4), "Next"),)
    await start_evening(callback, SessionStart(club_id=UUID(int=2)), state, service)
    keyboard = answer.await_args.kwargs["reply_markup"]
    pick = SessionPick.unpack(button(keyboard, "▫️ Next"))
    old_cancel = SessionStep.unpack(button(keyboard, ru.CANCEL_BUTTON))
    await pick_player(callback, pick, state, service)
    before = await state.get_data()
    await step(callback, old_cancel, state, service)
    assert await state.get_data() == before
    assert answer.await_args.args[0] == ru.SESSION_STALE


@pytest.mark.asyncio
async def test_start_entry_cannot_interrupt_another_flow(flow):
    state, callback, answer, service = flow
    await _screen(state, EveningFlow.selecting, club_id=str(UUID(int=3)), selected=[])
    before = await state.get_data()
    await start_evening(callback, SessionStart(club_id=UUID(int=2)), state, service)
    assert await state.get_data() == before
    service.active_players.assert_not_awaited()
    assert answer.await_args.args[0] == ru.SESSION_STALE


@pytest.mark.asyncio
async def test_legacy_payload_gets_friendly_reply_without_touching_state(flow):
    state, callback, answer, service = flow
    await _screen(state, EveningFlow.confirming, mode="finish")
    before = await state.get_data()
    await legacy_session_callback(callback.model_copy(update={"data": "step:finish"}))
    assert await state.get_data() == before
    assert answer.await_args.args[0] == ru.SESSION_STALE


@pytest.mark.asyncio
async def test_emergency_ui_creates_request_and_share_link(flow, monkeypatch):
    state, callback, answer, service = flow
    service.request_recovery.return_value = RecoveryView(UUID(int=8), UUID(int=1), "Next",
                                                         RecoveryStatus.PENDING, 0, False)
    link = AsyncMock(return_value="https://t.me/test_bot?start=recovery_0008")
    monkeypatch.setattr("app.bot.handlers.sessions.create_start_link", link)
    callback = callback.model_copy(update={"bot": AsyncMock()})
    await evening_action(callback, SessionAction(session_id=UUID(int=1), action="help"),
                         state, service, AsyncMock())
    choose = SessionStep.unpack(button(answer.await_args.kwargs["reply_markup"], ru.RECOVERY_START_BUTTON))
    await step(callback, choose, state, service)
    candidate = SessionPick.unpack(button(answer.await_args.kwargs["reply_markup"], "Next"))
    await pick_player(callback, candidate, state, service)
    confirm = SessionStep.unpack(button(answer.await_args.kwargs["reply_markup"], ru.CONFIRM_BUTTON))
    await step(callback, confirm, state, service)
    service.request_recovery.assert_awaited_once()
    assert service.request_recovery.await_args.args[:3] == (200, UUID(int=1), UUID(int=4))
    assert "https://t.me/test_bot" in answer.await_args.args[0]
    assert await state.get_state() is None


@pytest.mark.asyncio
async def test_recovery_deep_link_restores_request_without_fsm(flow):
    state, callback, answer, service = flow
    service.recovery_view.return_value = RecoveryView(UUID(int=8), UUID(int=1), "Next",
                                                      RecoveryStatus.PENDING, 1, True)
    await start(callback.message, CommandObject(command="start", args=f"recovery_{UUID(int=8).hex}"),
                AsyncMock(), service)
    service.recovery_view.assert_awaited_once_with(200, UUID(int=8))
    assert "1/2" in answer.await_args.args[0]
    confirm = RecoveryAction.unpack(button(answer.await_args.kwargs["reply_markup"], ru.RECOVERY_CONFIRM_BUTTON))
    service.confirm_recovery.return_value = RecoveryView(UUID(int=8), UUID(int=1), "Next",
                                                         RecoveryStatus.APPLIED, 2, False)
    await recovery_action(callback, confirm, service)
    assert answer.await_args.args[0] == "📋 Новый статист: Next"
    assert await state.get_state() is None


@pytest.mark.asyncio
async def test_malformed_recovery_link_has_friendly_error(flow):
    _, callback, answer, service = flow
    await start(callback.message, CommandObject(command="start", args="recovery_invalid"), AsyncMock(), service)
    service.recovery_view.assert_not_awaited()
    assert answer.await_args.args[0] == ru.SESSION_STALE


def test_session_payloads_fit_telegram_limit_and_recovery_is_not_main_menu():
    flow = "x" * 16
    players = tuple(RosterPlayer(UUID(int=i), f"Player {i}") for i in range(1, 13))
    view = SessionView(UUID(int=1), UUID(int=2), "Club", SessionStatus.ACTIVE,
                       "Stat", players, 0, 0, 0, True, UUID(int=3), True)
    markup = [keyboards.start_choices(players, set(), 1, flow), keyboards.player_choices(players, 1, flow),
              keyboards.confirm("recovery", flow), keyboards.flow_roster_actions(flow),
              keyboards.recovery_help(flow), keyboards.session_view(view),
              keyboards.recovery(RecoveryView(UUID(int=8), view.id, "Next", RecoveryStatus.PENDING, 0, True))]
    for keyboard in markup:
        for row in keyboard.inline_keyboard:
            for b in row:
                assert len(b.callback_data.encode()) <= 64
    labels = [b.text for row in keyboards.session_view(view).inline_keyboard for b in row]
    assert ru.SESSION_HELP_BUTTON in labels
    assert ru.RECOVERY_START_BUTTON not in labels


@pytest.mark.asyncio
async def test_dispatcher_routes_legacy_buttons_and_serializes_duplicate_finish(flow):
    import asyncio
    from aiogram import Bot
    from aiogram.types import Update
    from app.main import create_dispatcher

    _, callback, answer, service = flow
    bot = Bot(token="123456:example")
    dispatcher = create_dispatcher()
    state = dispatcher.fsm.get_context(bot=bot, chat_id=200, user_id=200)
    try:
        async def dispatch(data, update_id):
            query = callback.model_copy(update={"data": data, "id": str(update_id)})
            await dispatcher.feed_update(bot, Update(update_id=update_id, callback_query=query),
                                         session_service=service, game_service=AsyncMock())

        await dispatch(SessionAction(session_id=UUID(int=1), action="finish").pack(), 1)
        old_confirm = SessionStep.unpack(button(answer.await_args.kwargs["reply_markup"], ru.CONFIRM_BUTTON))
        await _screen(state, EveningFlow.confirming, session_id=str(UUID(int=99)), mode="finish",
                      expected_statistician_id=str(UUID(int=3)))
        before = await state.get_data()
        await dispatch("step:finish", 2)
        assert answer.await_args.args[0] == ru.SESSION_STALE
        assert await state.get_data() == before
        await dispatch(old_confirm.pack(), 3)
        assert answer.await_args.args[0] == ru.SESSION_STALE
        assert await state.get_data() == before
        service.finish.assert_not_awaited()

        async def finish(*args, **kwargs):
            await asyncio.sleep(0)
            return service.view.return_value

        service.finish.side_effect = finish
        current = SessionStep(flow=before["flow"], action="finish").pack()
        await asyncio.gather(dispatch(current, 4), dispatch(current, 5))
        service.finish.assert_awaited_once_with(200, UUID(int=99), expected_statistician_id=UUID(int=3))
        assert await state.get_state() is None
        assert answer.await_args.args[0] == ru.SESSION_STALE
    finally:
        await dispatcher.fsm.close()
        await bot.session.close()


@pytest.mark.asyncio
async def test_non_ascii_flow_token_is_rejected_without_exception(flow):
    state, callback, answer, service = flow
    await _screen(state, EveningFlow.confirming, mode="finish")
    before = await state.get_data()
    await step(callback, SessionStep(flow="неверный", action="finish"), state, service)
    assert await state.get_data() == before
    assert answer.await_args.args[0] == ru.SESSION_STALE
    service.finish.assert_not_awaited()


@pytest.mark.asyncio
async def test_share_link_failure_keeps_bound_request_for_safe_retry(flow, monkeypatch):
    state, callback, answer, service = flow
    token = await _screen(state, EveningFlow.confirming, session_id=str(UUID(int=1)), mode="recovery",
                          expected_statistician_id=str(UUID(int=3)), player_id=str(UUID(int=4)),
                          request_id=str(UUID(int=8)))
    service.request_recovery.return_value = RecoveryView(UUID(int=8), UUID(int=1), "Next",
                                                         RecoveryStatus.PENDING, 0, False)
    link = AsyncMock(side_effect=[RuntimeError("Temporary link failure"), "https://t.me/example"])
    monkeypatch.setattr("app.bot.handlers.sessions.create_start_link", link)
    confirm = SessionStep(flow=token, action="recovery")
    await step(callback, confirm, state, service)
    assert await state.get_state() == EveningFlow.confirming.state
    assert (await state.get_data())["request_id"] == str(UUID(int=8))
    assert answer.await_args.args[0] == ru.GENERIC_ERROR
    await step(callback, confirm, state, service)
    assert await state.get_state() is None
    assert "https://t.me/example" in answer.await_args.args[0]
    assert service.request_recovery.await_args_list[0] == service.request_recovery.await_args_list[1]


@pytest.mark.parametrize("count,available", [(0, False), (4, False), (5, True), (11, True)])
def test_record_button_requires_five_present_players(count, available):
    players = tuple(RosterPlayer(UUID(int=i + 1), f"Player {i}") for i in range(count))
    view = SessionView(UUID(int=100), UUID(int=200), "Club", SessionStatus.ACTIVE,
                       "Stat", players, 0, 0, 0, True)
    labels = [b.text for row in keyboards.session_view(view).inline_keyboard for b in row]
    assert (ru.RECORD_GAME_BUTTON in labels) is available
