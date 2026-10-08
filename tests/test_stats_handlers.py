"""Stateless Telegram statistics, pagination, private clubs and wizard coexistence."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
import pytest_asyncio
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from app.bot.callbacks import ClubAction, SessionAction, StatsPage, StatsPlayer, StatsSession
from app.bot.handlers.stats import legacy_profile, legacy_summary, page, player, session
from app.bot.keyboards import stats as keyboards
from app.bot.keyboards.membership import club_menu
from app.bot.keyboards.sessions import session_view
from app.domain.enums import EventType, GameRole, MembershipRole, RuinReason, SessionStatus, VictoryType
from app.domain.stats import LeaderboardKind
from app.locales import ru
from app.main import create_dispatcher
from app.services.membership import ClubView
from app.services.sessions import SessionView
from app.services.stats import ClubStats, Eligibility, Leaderboard, Page, PlayerStats, Score, SessionChoice, SessionStats, StatsError, StatsErrorCode, TeamStats

CLUB, PLAYER, EVENING = UUID(int=1), UUID(int=2), UUID(int=3)
NAME = '<b>Alice</b> & Markdown_*'


def example_player():
    return PlayerStats(PLAYER, NAME, True, 8, 5, {r: Score(4, 3) if r is GameRole.LIBERAL else Score(2, 1) for r in GameRole},
                       3, 2, {r: 2 if r is RuinReason.WRONG_VOTE else 1 if r is RuinReason.OTHER else 0 for r in RuinReason},
                       2, 1, 1, 1, Eligibility(8, 10), {r: Eligibility(4 if r is GameRole.LIBERAL else 2, 5) for r in GameRole})


@pytest.fixture
def service():
    service = AsyncMock()
    p = example_player()
    players = Page((p,), 0, 1)
    counts = {e: 3 if e is EventType.RUIN else 1 for e in EventType}
    victories = {v: 0 for v in VictoryType}
    service.get_my_stats.return_value = p
    service.player_profile.return_value = (CLUB, p)
    service.list_player_stats.return_value = players
    service.get_club_stats.return_value = ClubStats(CLUB, NAME, 2, TeamStats(8, 5, 3), counts, victories,
                                                   {i: TeamStats(0, 0, 0) for i in range(5, 11)})
    service.session_profile.return_value = SessionStats(EVENING, CLUB, datetime(2026, 1, 1, tzinfo=timezone.utc),
                                                       "Asia/Yakutsk", TeamStats(8, 5, 3), counts, victories, players)
    service.list_sessions.return_value = Page((SessionChoice(EVENING, datetime(2026, 1, 1, tzinfo=timezone.utc), "UTC"),), 0, 1)
    async def board(tid, cid, kind, **kwargs):
        return Leaderboard(kind, 10, players)
    service.get_leaderboard.side_effect = board
    return service


@pytest_asyncio.fixture
async def ui(monkeypatch):
    user = User(id=8801, is_bot=False, first_name="Member")
    msg = Message(message_id=1, date=datetime.now(timezone.utc), chat=Chat(id=8801, type="private"), from_user=user)
    callback = CallbackQuery(id="stats", from_user=user, chat_instance="test", message=msg)
    sent, answered = AsyncMock(), AsyncMock()
    monkeypatch.setattr(Message, "answer", sent)
    monkeypatch.setattr(CallbackQuery, "answer", answered)
    storage = MemoryStorage()
    state = FSMContext(storage, StorageKey(bot_id=1, chat_id=8801, user_id=8801))
    await state.set_state("GameFlow:active")
    await state.set_data({"flow": "new-game", "stage": "hitler", "draft_id": "original"})
    yield callback, sent, answered, state
    await storage.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("section", ["my", "menu", "ratings", "players", "roles", "ruin", "moments", "sessions", "club",
                                    "r_liberal", "r_fascist", "r_hitler"] + ["b_" + k.value for k in LeaderboardKind])
async def test_every_stats_screen_renders_plain_text_without_touching_fsm(ui, service, section):
    callback, sent, _, state = ui
    before = await state.get_data()
    await page(callback, StatsPage(club_id=CLUB, section=section), service)
    assert sent.await_count == 1
    assert sent.await_args.kwargs["parse_mode"] is None
    assert sent.await_args.kwargs["reply_markup"].inline_keyboard
    assert len(sent.await_args.args[0]) < 4096
    assert await state.get_data() == before and await state.get_state() == "GameFlow:active"


@pytest.mark.asyncio
async def test_profiles_and_session_stats_accept_member_and_legacy_buttons(ui, service):
    callback, sent, _, _ = ui
    await player(callback, StatsPlayer(player_id=PLAYER), service)
    service.player_profile.assert_awaited_with(8801, PLAYER)
    assert NAME in sent.await_args.args[0] and "62.5%" in sent.await_args.args[0]
    await session(callback, StatsSession(session_id=EVENING, page=2), service)
    service.session_profile.assert_awaited_with(8801, EVENING, page=2)
    assert "01.01.2026 09:00" in sent.await_args.args[0]  # Club timezone.
    await legacy_profile(callback, ClubAction(club_id=CLUB, action="profile"), service)
    service.get_my_stats.assert_awaited_with(8801, CLUB)
    await legacy_summary(callback, SessionAction(session_id=EVENING, action="summary"), service)
    service.session_profile.assert_awaited_with(8801, EVENING, page=0)


@pytest.mark.asyncio
@pytest.mark.parametrize("code", list(StatsErrorCode))
async def test_stats_errors_have_friendly_response(ui, service, code):
    callback, sent, answered, state = ui
    before = await state.get_data()
    service.get_my_stats.side_effect = StatsError(code)
    await page(callback, StatsPage(club_id=CLUB, section="my"), service)
    assert not sent.await_count
    assert answered.await_args.args == (ru.STATS_ERRORS[code.value],)
    assert await state.get_data() == before


@pytest.mark.asyncio
async def test_invalid_board_callback_is_safe(ui, service):
    callback, sent, answered, _ = ui
    await page(callback, StatsPage(club_id=CLUB, section="b_nothing"), service)
    assert not sent.await_count
    assert answered.await_args.args == (ru.STATS_ERRORS["INVALID_BOARD"],)


@pytest.mark.asyncio
async def test_old_pagination_reloads_current_data_and_service_checks_scope(ui, service):
    callback, sent, answered, _ = ui
    service.list_player_stats.return_value = Page((), 0, 0)
    await page(callback, StatsPage(club_id=CLUB, section="players", page=999), service)
    service.list_player_stats.assert_awaited_with(8801, CLUB, page=999)
    assert ru.STATS_NO_PLAYERS in sent.await_args.args[0]
    service.list_player_stats.side_effect = StatsError(StatsErrorCode.NO_ACCESS)
    await page(callback, StatsPage(club_id=UUID(int=999), section="players"), service)
    assert answered.await_args.args == (ru.STATS_ERRORS["NO_ACCESS"],)


@pytest.mark.asyncio
@pytest.mark.parametrize("section", ["ruin", "r_liberal", "r_fascist", "r_hitler"])
async def test_statistics_lists_include_players_below_leaderboard_threshold(ui, service, section):
    callback, sent, _, _ = ui
    await page(callback, StatsPage(club_id=CLUB, section=section), service)
    service.list_player_stats.assert_awaited_once_with(8801, CLUB, page=0)
    service.get_leaderboard.assert_not_awaited()
    assert NAME in sent.await_args.args[0]


@pytest.mark.asyncio
async def test_empty_leaderboard_sessions_and_zero_player(ui, service):
    callback, sent, _, _ = ui
    service.get_leaderboard.side_effect = None
    service.get_leaderboard.return_value = Leaderboard(LeaderboardKind.HITLER, 5, Page((), 0, 0))
    await page(callback, StatsPage(club_id=CLUB, section="b_hitler"), service)
    assert "минимум 5 игр" in sent.await_args.args[0]
    service.list_sessions.return_value = Page((), 0, 0)
    await page(callback, StatsPage(club_id=CLUB, section="sessions"), service)
    assert sent.await_args.args[0] == ru.STATS_NO_SESSIONS
    assert ru.stats_percent(0) == "0.0%"
    assert ru.stats_percent(100 / 3) == "33.3%"


def test_all_menus_and_pagination_callbacks_fit_telegram_limit():
    p = example_player()
    menus = [keyboards.menu(CLUB), keyboards.menu(CLUB, ratings=True), keyboards.menu(CLUB, roles=True),
             keyboards.page_view(CLUB, "players", Page((p,), 1, 30), players=True),
             keyboards.page_view(CLUB, "b_fascist", Page((p,), 1, 30), players=True, back="ratings"),
             club_menu(ClubView(CLUB, NAME, MembershipRole.PLAYER, None, False))]
    view = SessionView(EVENING, CLUB, "Club", SessionStatus.ACTIVE, NAME, (), 0, 0, 0, False)
    menus.append(session_view(view))
    assert any(b.text == ru.STATS_SESSION_BUTTON for row in menus[-1].inline_keyboard for b in row)
    for menu in menus:
        for row in menu.inline_keyboard:
            for b in row:
                assert len(b.callback_data.encode()) <= 64
    menu_labels = {b.text for row in menus[-2].inline_keyboard for b in row}
    assert {ru.STATS_MY_BUTTON, ru.STATS_BUTTON, ru.RATINGS_BUTTON} <= menu_labels
    nav = keyboards.pagination(Page((p,), 1, 30), lambda n: StatsPage(club_id=CLUB, section="players", page=n))
    assert [StatsPage.unpack(b.callback_data).page for b in nav] == [0, 1, 2]


@pytest.mark.asyncio
async def test_real_dispatcher_routes_stats_and_legacy_summary_without_clearing_game(ui, service):
    from app import main
    # Older session tests leave module routers attached after closing their dispatcher.
    routers = [main.start_router, main.stats_router, main.history_router, main.membership_router, main.admin_router,
               main.sessions_router, main.games_router, main.events_router]
    for r in routers:
        r._parent_router = None
    dispatcher = create_dispatcher()
    bot = Bot(token="123456:abcdefghijklmnopqrstuvwxyzABCDEF", default=DefaultBotProperties(parse_mode="HTML"))
    state = dispatcher.fsm.get_context(bot=bot, chat_id=8801, user_id=8801)
    await state.set_state("GameFlow:active")
    await state.set_data({"flow": "preserved"})
    callback, sent, _, _ = ui
    try:
        for index, payload in enumerate((StatsPage(club_id=CLUB, section="my"), StatsPlayer(player_id=PLAYER),
                                         StatsSession(session_id=EVENING), ClubAction(club_id=CLUB, action="profile"),
                                         SessionAction(session_id=EVENING, action="summary"))):
            query = callback.model_copy(update={"data": payload.pack(), "id": str(index)})
            await dispatcher.feed_update(bot, Update(update_id=index, callback_query=query), stats_service=service)
        assert sent.await_count == 5
        assert all(c.kwargs["parse_mode"] is None for c in sent.await_args_list)
        assert await state.get_data() == {"flow": "preserved"}
        assert await state.get_state() == "GameFlow:active"
    finally:
        await dispatcher.fsm.close()
        await bot.session.close()
        for r in dispatcher.sub_routers:
            r._parent_router = None
