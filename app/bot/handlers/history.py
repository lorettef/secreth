"""History and corrections: drafts in FSM, authorization and writes in services."""

import logging
import secrets
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, ForceReply, Message

from app.bot.callbacks import GameCard, HistoryMutation, HistoryPage, HistoryPick, HistoryStep
from app.bot.handlers.events import _reply
from app.bot.keyboards import history as keyboards
from app.domain.enums import GameRole, GameStatus, Team, VictoryType
from app.domain.roles import validate_player_count
from app.domain.victories import VICTORY_WINNERS, validate_victory
from app.locales import ru
from app.services.events import EventError, EventService
from app.services.games import GameError, GameErrorCode, GamePerson, GameService
from app.services.history import HistoryService
from app.services.stats import StatsError, StatsService

router = Router(name="history")
logger = logging.getLogger(__name__)


class HistoryFlow(StatesGroup):
    active = State()


def date(value: datetime, timezone: str, *, short: bool = False) -> str:
    return value.astimezone(ZoneInfo(timezone)).strftime("%d.%m" if short else "%d.%m.%Y %H:%M")


async def error(callback: CallbackQuery, exc: Exception) -> None:
    if isinstance(exc, (GameError, EventError)):
        text = ru.HISTORY_ERRORS.get(exc.code.value, ru.GENERIC_ERROR)
    elif isinstance(exc, StatsError):
        text = ru.STATS_ERRORS[exc.code.value]
    else:
        logger.exception("History interaction failed")
        text = ru.GENERIC_ERROR
    await _reply(callback, text)


async def show_card(callback: CallbackQuery, history_service: HistoryService, game_id: UUID, event_page: int = 0) -> None:
    view = await history_service.card(callback.from_user.id, game_id, event_page=event_page)
    await _reply(callback, ru.history_card(view, date(view.game.played_at, view.game.timezone)), keyboards.card(view))
    await _reply(callback, ru.history_events(view.events, view.game.sequence_number), keyboards.events(view))


@router.callback_query(GameCard.filter())
async def game_card(callback: CallbackQuery, callback_data: GameCard, history_service: HistoryService) -> None:
    try:
        await show_card(callback, history_service, callback_data.game_id)
    except Exception as exc:
        await error(callback, exc)


@router.callback_query(HistoryPage.filter())
async def page(callback: CallbackQuery, callback_data: HistoryPage, history_service: HistoryService,
               stats_service: StatsService, event_service: EventService) -> None:
    tid, scope, section, n = callback.from_user.id, callback_data.scope_id, callback_data.section, callback_data.page
    try:
        if section == "menu":
            await history_service.club_access(tid, scope)
            await _reply(callback, ru.HISTORY_BUTTON, keyboards.menu(scope))
            return
        if section in ("sessions", "recent", "players", "manage", "audit"):
            club = scope
        elif section in ("session", "pgames", "player", "entry"):
            club = await history_service.object_club(tid, {"session": "session", "pgames": "player",
                                                       "player": "player", "entry": "audit"}[section], scope,
                                                      admin=section in ("player", "entry"))
        elif section == "game":
            await show_card(callback, history_service, scope, n)
            return
        elif section == "event":
            event = await event_service.event(tid, scope)
            card = await history_service.card(tid, event.game_id)
            can_edit = card.can_edit or card.is_admin
            rev = (await event_service.edit_setup(tid, scope))[2] if can_edit else ""
            await _reply(callback, ru.event_card(event.player_name, event.event_type, event.ruin_reason,
                         event.self_admitted, event.comment), keyboards.event(event, rev, can_edit, card.is_admin))
            return
        else:
            raise GameError(GameErrorCode.STALE)
        if section == "sessions":
            result = await history_service.list_sessions(tid, club, page=n)
            choices = [(ru.history_session_label(s, date(s.started_at, s.timezone)),
                        HistoryPage(scope_id=s.id, section="session")) for s in result.items]
        elif section in ("recent", "session", "pgames"):
            result = await history_service.list_games(tid, club, page=n,
                         session_id=scope if section == "session" else None, player_id=scope if section == "pgames" else None)
            choices = [(ru.history_game_label(g, date(g.played_at, g.timezone, short=True)), GameCard(game_id=g.id)) for g in result.items]
        elif section in ("players", "manage"):
            result = await history_service.list_players(tid, club, page=n, admin=section == "manage")
            choices = [(p.name + (ru.HISTORY_INACTIVE if not p.is_active else ""),
                        HistoryPage(scope_id=p.id, section="player" if section == "manage" else "pgames")) for p in result.items]
        elif section == "audit":
            result = await history_service.list_audit(tid, club, page=n)
            choices = [(ru.history_audit_label(e, date(e.created_at, e.timezone)), HistoryPage(scope_id=e.id, section="entry")) for e in result.items]
        elif section == "player":
            _, p = await history_service.player(tid, scope)
            await _reply(callback, ru.history_player_card(p), keyboards.player(p, club))
            return
        else:  # entry
            _, entry = await history_service.audit(tid, scope)
            await _reply(callback, ru.history_audit_detail(entry, date(entry.created_at, entry.timezone)), keyboards.menu(club))
            return
        if section == "session":
            stats = await stats_service.get_session_stats(tid, club, scope)
            text = ru.stats_session_card(stats, date(stats.started_at, stats.timezone))
        else:
            text = ru.HISTORY_SECTIONS.get(section, ru.HISTORY_PLAYERS_ADMIN if section == "manage" else ru.HISTORY_AUDIT)
        await _reply(callback, text + ("\n" + ru.HISTORY_EMPTY if not result.total else ""),
                     keyboards.listing(scope, section, result, choices, club_id=club))
    except Exception as exc:
        await error(callback, exc)


async def require(state: FSMContext, flow: str) -> dict:
    data = await state.get_data()
    if (await state.get_state() != HistoryFlow.active.state or not flow or not flow.isascii()
            or not secrets.compare_digest(flow, data.get("flow", ""))):
        raise GameError(GameErrorCode.STALE)
    return data


async def screen(callback: CallbackQuery, state: FSMContext, data: dict, **changes) -> None:
    data = {**data, **changes, "flow": secrets.token_urlsafe(12), "prompt_id": None}
    flow, stage = data["flow"], data["stage"]
    if stage == "menu":
        text = ru.HISTORY_EDIT_MENU + "\n\n" + ru.history_edit_preview(data)
        markup = keyboards.edit_menu(flow)
    elif stage in ("participants", "hitler", "fascists"):
        people = [GamePerson(UUID(pid), name) for pid, name in data["candidates"]]
        if stage != "participants":
            people = [p for p in people if str(p.id) in data["participants"]]
        if stage == "fascists":
            people = [p for p in people if str(p.id) != data.get("hitler")]
        if stage == "participants":
            selected = {UUID(pid) for pid in data["participants"]}
        elif stage == "hitler":
            selected = {UUID(data["hitler"])} if data.get("hitler") else set()
        else:
            selected = {UUID(pid) for pid in data["fascists"]}
        text = ru.HISTORY_EDIT_FIELDS[stage]
        markup = keyboards.picks(people, selected, flow, data.get("page", 0), multiple=stage != "hitler")
    elif stage == "winner":
        text = ru.HISTORY_EDIT_FIELDS[stage]
        markup = keyboards.choices(ru.HISTORY_TEAM_CHOICES, flow)
    elif stage == "victory":
        text = ru.HISTORY_EDIT_FIELDS[stage]
        markup = keyboards.choices({"v_" + v.value: ru.STATS_VICTORY_LABELS[v.value] for v, team in VICTORY_WINNERS.items()
                                    if team.value == data["winner"]}, flow)
    elif stage == "rename":
        text, markup = ru.HISTORY_RENAME_PROMPT, keyboards.choices({}, flow)
    else:
        text = ru.HISTORY_CONFIRM[data["intent"]]
        if data["intent"] == "pn":
            text += "\n" + data["name"]
        markup = keyboards.confirm(flow)
    await state.set_data(data)
    await _reply(callback, text, markup)
    if stage == "rename" and isinstance(callback.message, Message):
        msg = await callback.message.answer(ru.HISTORY_RENAME_PROMPT, parse_mode=None, reply_markup=ForceReply(selective=True))
        await state.update_data(prompt_id=msg.message_id)


@router.callback_query(HistoryMutation.filter())
async def begin(callback: CallbackQuery, callback_data: HistoryMutation, state: FSMContext,
                history_service: HistoryService, event_service: EventService) -> None:
    try:
        if await state.get_state() is not None:
            raise GameError(GameErrorCode.STALE)
        tid, target, intent = callback.from_user.id, callback_data.entity_id, callback_data.action
        data = {"entity_id": str(target), "intent": intent, "revision": callback_data.revision, "stage": "confirm"}
        if intent in ("ge", "gv", "gr"):
            card = await history_service.card(tid, target)
            if not (card.can_edit if intent == "ge" else card.is_admin):
                raise GameError(GameErrorCode.NO_ACCESS)
            if card.revision != callback_data.revision:
                raise GameError(GameErrorCode.STALE)
            if intent in ("gv", "gr") and card.game.status is not (GameStatus.VOID if intent == "gr" else GameStatus.ACTIVE):
                raise GameError(GameErrorCode.STALE)
            data["club_id"] = str(card.club_id)
            if intent == "ge":
                people = await history_service.edit_candidates(tid, target)
                data.update(stage="menu", candidates=[(str(p.id), p.name) for p in people],
                            participants=[str(p.id) for p in card.participants],
                            hitler=next((str(pid) for pid, role in card.roles.items() if role is GameRole.HITLER), None),
                            fascists=[str(pid) for pid, role in card.roles.items() if role is GameRole.FASCIST],
                            winner=card.game.winner.value, victory=card.victory_type.value)
        elif intent in ("ee", "ed"):
            event, setup, rev = await event_service.edit_setup(tid, target)
            if rev != callback_data.revision:
                raise GameError(GameErrorCode.STALE)
            club = await history_service.object_club(tid, "event", target, admin=intent == "ed")
            if intent == "ee":
                from app.bot.handlers.events import begin_edit
                await begin_edit(callback, state, event, setup, rev)
                return
            data.update(game_id=str(event.game_id), club_id=str(club))
        elif intent in ("pn", "pd", "pr"):
            club, p = await history_service.player(tid, target)
            if p.revision != callback_data.revision:
                raise GameError(GameErrorCode.STALE)
            data.update(club_id=str(club), stage="rename" if intent == "pn" else "confirm")
        else:
            raise GameError(GameErrorCode.STALE)
        await state.set_state(HistoryFlow.active)
        await screen(callback, state, data)
    except Exception as exc:
        await error(callback, exc)


@router.callback_query(HistoryPick.filter())
async def pick(callback: CallbackQuery, callback_data: HistoryPick, state: FSMContext) -> None:
    try:
        data = await require(state, callback_data.flow)
        stage, pid = data["stage"], str(callback_data.player_id)
        if pid not in {p for p, _ in data.get("candidates", [])} or stage not in ("participants", "hitler", "fascists"):
            raise GameError(GameErrorCode.STALE)
        if stage != "participants" and pid not in data["participants"]:
            raise GameError(GameErrorCode.STALE)
        if stage == "fascists" and pid == data.get("hitler"):
            raise GameError(GameErrorCode.STALE)
        if stage == "hitler":
            await screen(callback, state, data, hitler=pid, fascists=[p for p in data["fascists"] if p != pid], stage="menu")
        else:
            key = "participants" if stage == "participants" else "fascists"
            values = list(data[key])
            values.remove(pid) if pid in values else values.append(pid)
            await screen(callback, state, data, **{key: values})
    except Exception as exc:
        await error(callback, exc)


async def finish(callback: CallbackQuery, state: FSMContext, data: dict, history_service: HistoryService) -> None:
    await state.clear()
    if data["intent"] in ("ge", "gv", "gr"):
        await show_card(callback, history_service, UUID(data["entity_id"]))
    elif data["intent"] == "ed":
        await show_card(callback, history_service, UUID(data["game_id"]))
    else:
        club, p = await history_service.player(callback.from_user.id, UUID(data["entity_id"]))
        await _reply(callback, ru.history_player_card(p), keyboards.player(p, club))


@router.callback_query(HistoryStep.filter())
async def step(callback: CallbackQuery, callback_data: HistoryStep, state: FSMContext,
               history_service: HistoryService, game_service: GameService, event_service: EventService) -> None:
    try:
        data = await require(state, callback_data.flow)
        code, tid, target = callback_data.action, callback.from_user.id, UUID(data["entity_id"])
        if code == "cancel":
            await finish(callback, state, data, history_service)
            await _reply(callback, ru.HISTORY_CANCELLED)
        elif code == "back":
            if data["intent"] == "ge":
                if data["stage"] not in ru.HISTORY_EDIT_FIELDS:
                    raise GameError(GameErrorCode.STALE)
                if data["stage"] == "participants":
                    try:
                        validate_player_count(len(data["participants"]))
                    except ValueError:
                        raise GameError(GameErrorCode.INVALID_ROLES) from None
                    data = {**data, "hitler": data.get("hitler") if data.get("hitler") in data["participants"] else None,
                            "fascists": [pid for pid in data["fascists"] if pid in data["participants"]]}
            elif data["intent"] != "pn":
                raise GameError(GameErrorCode.STALE)
            await screen(callback, state, data, stage="menu" if data["intent"] == "ge" else "rename")
        elif data["intent"] == "ge":
            if code in ru.HISTORY_EDIT_FIELDS and data["stage"] == "menu":
                await screen(callback, state, data, stage=code, page=0)
            elif code in ("prev", "next") and data["stage"] in ("participants", "hitler", "fascists"):
                n = data.get("page", 0) + (1 if code == "next" else -1)
                if n < 0:
                    raise GameError(GameErrorCode.STALE)
                await screen(callback, state, data, page=n)
            elif code in ("win_L", "win_F") and data["stage"] == "winner":
                await screen(callback, state, data, winner=Team.LIBERAL.value if code == "win_L" else Team.FASCIST.value,
                             victory=None, stage="victory")
            elif code.startswith("v_") and data["stage"] == "victory":
                try:
                    victory = VictoryType(code[2:])
                    validate_victory(Team(data["winner"]), victory)
                except ValueError:
                    raise GameError(GameErrorCode.INVALID_VICTORY) from None
                await screen(callback, state, data, victory=victory.value, stage="menu")
            elif code == "save" and data["stage"] == "menu":
                if not data.get("hitler") or not data.get("victory"):
                    raise GameError(GameErrorCode.INVALID_ROLES if not data.get("hitler") else GameErrorCode.INVALID_VICTORY)
                await game_service.edit_game(tid, target, expected_revision=data["revision"],
                    participants=tuple(UUID(p) for p in data["participants"]), hitler=UUID(data["hitler"]),
                    fascists=tuple(UUID(p) for p in data["fascists"]), winner=Team(data["winner"]), victory_type=VictoryType(data["victory"]))
                await finish(callback, state, data, history_service)
                await _reply(callback, ru.HISTORY_SAVED)
            else:
                raise GameError(GameErrorCode.STALE)
        elif code == "confirm" and data["stage"] == "confirm":
            if data["intent"] in ("gv", "gr"):
                await game_service.set_game_void(tid, target, expected_revision=data["revision"], restore=data["intent"] == "gr")
            elif data["intent"] == "ed":
                await event_service.delete_event(tid, target, expected_revision=data["revision"])
            elif data["intent"] in ("pn", "pd", "pr"):
                await history_service.edit_player(tid, target, expected_revision=data["revision"],
                    display_name=data.get("name") if data["intent"] == "pn" else None,
                    is_active=data["intent"] == "pr" if data["intent"] in ("pd", "pr") else None)
            else:
                raise GameError(GameErrorCode.STALE)
            await finish(callback, state, data, history_service)
            await _reply(callback, ru.HISTORY_SAVED)
        else:
            raise GameError(GameErrorCode.STALE)
    except Exception as exc:
        await error(callback, exc)


@router.message(HistoryFlow.active, F.text)
async def rename(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if (await state.get_state() != HistoryFlow.active.state or data.get("stage") != "rename" or not data.get("prompt_id") or not message.reply_to_message
            or message.reply_to_message.message_id != data["prompt_id"]):
        await message.answer(ru.HISTORY_ERRORS["STALE"], parse_mode=None)
        return
    name = message.text.strip()
    if not name or len(name) > 255:
        await message.answer(ru.HISTORY_ERRORS["INVALID_NAME"], parse_mode=None)
        return
    data = {**data, "stage": "confirm", "name": name, "flow": secrets.token_urlsafe(12), "prompt_id": None}
    await state.set_data(data)
    await message.answer(ru.HISTORY_CONFIRM["pn"] + "\n" + name, parse_mode=None, reply_markup=keyboards.confirm(data["flow"]))


@router.callback_query(F.data.startswith(("hp:", "hm:", "hs:", "hi:")))
async def malformed(callback: CallbackQuery) -> None:
    await _reply(callback, ru.HISTORY_ERRORS["STALE"])
