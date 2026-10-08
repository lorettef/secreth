"""Read-only screens; viewing statistics never alters a wizard's FSM."""

from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.types import CallbackQuery

from app.bot.callbacks import ClubAction, SessionAction, StatsPage, StatsPlayer, StatsSession
from app.bot.keyboards import stats as keyboards
from app.domain.stats import LeaderboardKind
from app.locales import ru
from app.services.stats import StatsError, StatsErrorCode, StatsService

router = Router(name="stats")


def date_label(item) -> str:
    return item.started_at.astimezone(ZoneInfo(item.timezone)).strftime("%d.%m.%Y %H:%M")


async def render(callback: CallbackQuery, text: str, markup) -> None:
    await callback.answer()
    if callback.message:
        # Names and comments can contain Telegram markup. These screens use plain text.
        await callback.message.answer(text, reply_markup=markup, parse_mode=None)


async def error(callback: CallbackQuery, exc: StatsError) -> None:
    await callback.answer(ru.STATS_ERRORS[exc.code.value], show_alert=True)


@router.callback_query(StatsPage.filter())
async def page(callback: CallbackQuery, callback_data: StatsPage, stats_service: StatsService) -> None:
    tid, club, section = callback.from_user.id, callback_data.club_id, callback_data.section
    try:
        if section in ("menu", "ratings", "roles"):
            # Menu itself also verifies membership; no callback is trusted for club access.
            await stats_service.get_club_stats(tid, club)
            text = ru.RATINGS_BUTTON if section == "ratings" else ru.STATS_SECTIONS["roles"] if section == "roles" else ru.STATS_BUTTON
            markup = keyboards.menu(club, ratings=section == "ratings", roles=section == "roles")
        elif section == "my":
            text = ru.stats_player_card(await stats_service.get_my_stats(tid, club))
            markup = keyboards.back(club)
        elif section in ("players", "moments"):
            result = await stats_service.list_player_stats(tid, club, page=callback_data.page)
            text = ru.stats_players(result, moments=section == "moments")
            markup = keyboards.page_view(club, section, result, players=True)
        elif section == "ruin" or section in ("r_liberal", "r_fascist", "r_hitler"):
            result = await stats_service.list_player_stats(tid, club, page=callback_data.page)
            text = ru.stats_ruin_players(result) if section == "ruin" else ru.stats_role_players(result, section[2:].upper())
            markup = keyboards.page_view(club, section, result, players=True,
                                         back="menu" if section == "ruin" else "roles")
        elif section == "sessions":
            result = await stats_service.list_sessions(tid, club, page=callback_data.page)
            text = ru.STATS_SECTIONS["sessions"] if result.total else ru.STATS_NO_SESSIONS
            markup = keyboards.page_view(club, section, result, sessions=True, date_label=date_label)
        elif section == "club":
            text = ru.stats_club_card(await stats_service.get_club_stats(tid, club))
            markup = keyboards.back(club)
        elif section.startswith("b_"):
            try:
                kind = LeaderboardKind(section[2:])
            except ValueError:
                raise StatsError(StatsErrorCode.INVALID_BOARD) from None
            result = await stats_service.get_leaderboard(tid, club, kind, page=callback_data.page)
            text = ru.stats_leaderboard(result)
            markup = keyboards.page_view(club, section, result.entries, players=True, back="ratings")
        else:
            raise StatsError(StatsErrorCode.INVALID_BOARD)
        await render(callback, text, markup)
    except StatsError as exc:
        await error(callback, exc)


@router.callback_query(StatsPlayer.filter())
async def player(callback: CallbackQuery, callback_data: StatsPlayer, stats_service: StatsService) -> None:
    try:
        club, result = await stats_service.player_profile(callback.from_user.id, callback_data.player_id)
        await render(callback, ru.stats_player_card(result), keyboards.back(club))
    except StatsError as exc:
        await error(callback, exc)


@router.callback_query(StatsSession.filter())
async def session(callback: CallbackQuery, callback_data: StatsSession, stats_service: StatsService) -> None:
    try:
        result = await stats_service.session_profile(callback.from_user.id, callback_data.session_id, page=callback_data.page)
        await render(callback, ru.stats_session_card(result, date_label(result)), keyboards.session_view(result))
    except StatsError as exc:
        await error(callback, exc)


# Preserve already sent profile/summary buttons, including finished evenings.
@router.callback_query(ClubAction.filter(F.action == "profile"))
async def legacy_profile(callback: CallbackQuery, callback_data: ClubAction, stats_service: StatsService) -> None:
    await page(callback, StatsPage(club_id=callback_data.club_id, section="my"), stats_service)


@router.callback_query(SessionAction.filter(F.action == "summary"))
async def legacy_summary(callback: CallbackQuery, callback_data: SessionAction, stats_service: StatsService) -> None:
    await session(callback, StatsSession(session_id=callback_data.session_id), stats_service)
