"""Fast FSM wizard for completed games; no draft rows until Save."""

import logging
import secrets
import uuid
from uuid import UUID

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery

from app.bot.callbacks import GameAction, GameAfter, GameChoice, GamePick, GameSave
from app.bot.handlers.common import reply
from app.bot.keyboards import games as keyboards
from app.bot.keyboards.sessions import roster_actions, session_view
from app.domain.enums import SessionStatus, Team, VictoryType
from app.domain.roles import assign_liberals, get_role_distribution
from app.domain.victories import VictoryValidationError, validate_victory
from app.locales import ru
from app.services.games import GameError, GameErrorCode, GamePerson, GameResult, GameService
from app.services.sessions import SessionError, SessionService

router = Router()
logger = logging.getLogger(__name__)

VICTORIES = {"LP": VictoryType.LIBERAL_POLICIES, "HK": VictoryType.HITLER_KILLED,
             "FP": VictoryType.FASCIST_POLICIES, "HC": VictoryType.HITLER_CHANCELLOR}
VICTORY_LABELS = {
    VictoryType.LIBERAL_POLICIES: "5 либеральных законов",
    VictoryType.HITLER_KILLED: "Hitler убит",
    VictoryType.FASCIST_POLICIES: "6 фашистских законов",
    VictoryType.HITLER_CHANCELLOR: "Hitler избран канцлером",
}


class GameFlow(StatesGroup):
    active = State()


def _people(data: dict) -> tuple[GamePerson, ...]:
    return tuple(GamePerson(UUID(pid), name) for pid, name in data["candidates"])


def _selected(data: dict) -> tuple[GamePerson, ...]:
    ids = {UUID(pid) for pid in data["participants"]}
    return tuple(p for p in _people(data) if p.id in ids)


def _name(data: dict, pid: UUID) -> str:
    return next(p.name for p in _people(data) if p.id == pid)


async def _error(callback: CallbackQuery, error: Exception) -> None:
    if isinstance(error, GameError):
        await reply(callback, ru.GAME_ERRORS[error.code.value])
    elif isinstance(error, SessionError):
        await reply(callback, ru.SESSION_ERRORS[error.code.value])
    elif isinstance(error, VictoryValidationError):
        await reply(callback, ru.GAME_ERRORS["INVALID_VICTORY"])
    else:
        logger.exception("Game wizard failed")
        await reply(callback, ru.GENERIC_ERROR)


async def begin_wizard(callback: CallbackQuery, state: FSMContext, service: GameService,
                       session_id: UUID, same_participants: tuple[UUID, ...] | None = None) -> None:
    if await state.get_state() is not None:
        raise GameError(GameErrorCode.STALE)
    setup = await service.setup(callback.from_user.id, session_id)
    candidate_ids = {p.id for p in setup.candidates}
    if same_participants is not None and set(same_participants) <= candidate_ids:
        selected = same_participants
    elif len(setup.roster) <= 10:
        selected = tuple(p.id for p in setup.roster)
    else:
        selected = ()
    stage = "hitler" if 5 <= len(selected) <= 10 else "participants"
    data = {
        "session_id": str(session_id), "draft_id": str(uuid.uuid4()),
        "sequence": setup.sequence_number, "stage": stage, "page": 0,
        "candidates": [(str(p.id), p.name) for p in setup.candidates],
        "participants": [str(pid) for pid in selected], "fascists": [],
    }
    await state.set_state(GameFlow.active)
    data = await _screen(state, data)
    if stage == "hitler":
        await reply(callback, f"🎲 Игра #{setup.sequence_number}\n👑 Кто был Hitler?\n"
                    "Текущий состав выбран автоматически; его можно изменить.",
                    keyboards.hitler(_selected(data), data["flow"]))
    else:
        await reply(callback, f"🎲 Игра #{setup.sequence_number}\n"
                    f"Присутствуют {len(setup.roster)} игроков. Выберите 5–10 участников партии (0/10).",
                    keyboards.participants(setup.candidates, set(), 0, data["flow"]))


async def _screen(state: FSMContext, data: dict, **changes) -> dict:
    # A fresh token also invalidates buttons from earlier visits to the same stage.
    current = {**data, **changes, "flow": secrets.token_urlsafe(12)}
    await state.set_data(current)
    return current


async def _require_data(state: FSMContext, flow: str, stage: str | None = None) -> dict:
    data = await state.get_data()
    if (await state.get_state() != GameFlow.active.state or not data.get("draft_id")
            or not flow or not flow.isascii() or not secrets.compare_digest(flow, data.get("flow", ""))
            or (stage is not None and data.get("stage") != stage)):
        raise GameError(GameErrorCode.STALE)
    return data


@router.callback_query(GamePick.filter())
async def pick(callback: CallbackQuery, callback_data: GamePick, state: FSMContext) -> None:
    try:
        step = {"p": "participants", "h": "hitler", "f": "fascists"}.get(callback_data.step)
        if step is None:
            raise GameError(GameErrorCode.STALE)
        data = await _require_data(state, callback_data.flow, step)
        pid = callback_data.player_id
        if step == "participants":
            if pid not in {p.id for p in _people(data)}:
                raise GameError(GameErrorCode.INVALID_PARTICIPANTS)
            selected = {UUID(value) for value in data["participants"]}
            if pid in selected:
                selected.remove(pid)
            elif len(selected) < 10:
                selected.add(pid)
            else:
                raise GameError(GameErrorCode.INVALID_PARTICIPANTS)
            data = await _screen(state, data, participants=[str(value) for value in selected],
                                    hitler=None, fascists=[], winner=None, victory=None)
            await reply(callback, f"👥 Выберите участников партии — {len(selected)}/10 (минимум 5)",
                        keyboards.participants(_people(data), selected, data["page"], data["flow"]))
        elif step == "hitler":
            if pid not in {p.id for p in _selected(data)}:
                raise GameError(GameErrorCode.INVALID_ROLES)
            data = await _screen(state, data, stage="fascists", hitler=str(pid), fascists=[],
                                    winner=None, victory=None)
            await _show_fascists(callback, data)
        elif step == "fascists":
            if pid == UUID(data["hitler"]) or pid not in {p.id for p in _selected(data)}:
                raise GameError(GameErrorCode.INVALID_ROLES)
            required = get_role_distribution(len(data["participants"])).fascists
            selected = {UUID(value) for value in data["fascists"]}
            if pid in selected:
                selected.remove(pid)
            elif len(selected) < required:
                selected.add(pid)
            else:
                raise GameError(GameErrorCode.INVALID_ROLES)
            data = await _screen(state, data, fascists=[str(value) for value in selected],
                                    winner=None, victory=None)
            await _show_fascists(callback, data)
    except Exception as error:
        await _error(callback, error)


async def _show_fascists(callback: CallbackQuery, data: dict) -> None:
    hitler = UUID(data["hitler"])
    required = get_role_distribution(len(data["participants"])).fascists
    chosen = {UUID(value) for value in data["fascists"]}
    players = tuple(p for p in _selected(data) if p.id != hitler)
    await reply(callback, f"😈 Выберите Fascists — {len(chosen)}/{required}",
                keyboards.fascists(players, chosen, required, data["flow"]))


@router.callback_query(GameAction.filter())
async def action(callback: CallbackQuery, callback_data: GameAction, state: FSMContext,
                 session_service: SessionService) -> None:
    try:
        if callback_data.action == "cancel":
            data = await _require_data(state, callback_data.flow)
            view = await session_service.view(callback.from_user.id, UUID(data["session_id"]))
            await state.clear()
            await reply(callback, "Запись партии отменена.\n" + ru.session_message(view), session_view(view))
            return
        data = await _require_data(state, callback_data.flow)
        step = callback_data.action
        if step == "participants" and data["stage"] == "hitler":
            data = await _screen(state, data, stage="participants", page=0)
            await reply(callback, f"👥 Выберите участников — {len(data['participants'])}/10 (минимум 5)",
                        keyboards.participants(_people(data),
                                               {UUID(pid) for pid in data["participants"]}, 0, data["flow"]))
        elif step in ("next", "prev") and data["stage"] == "participants":
            page = max(0, min((len(data["candidates"]) - 1) // 10,
                              data["page"] + (1 if step == "next" else -1)))
            data = await _screen(state, data, page=page)
            await reply(callback, f"👥 Выберите участников — {len(data['participants'])}/10 (минимум 5)",
                        keyboards.participants(_people(data),
                                               {UUID(pid) for pid in data["participants"]}, page, data["flow"]))
        elif step == "participants_done" and data["stage"] == "participants":
            if not 5 <= len(data["participants"]) <= 10:
                raise GameError(GameErrorCode.INVALID_PARTICIPANTS)
            data = await _screen(state, data, stage="hitler", hitler=None, fascists=[], winner=None, victory=None)
            await reply(callback, "👑 Кто был Hitler?", keyboards.hitler(_selected(data), data["flow"]))
        elif step == "hitler_back" and data["stage"] == "fascists":
            data = await _screen(state, data, stage="hitler", hitler=None, fascists=[])
            await reply(callback, "👑 Кто был Hitler?", keyboards.hitler(_selected(data), data["flow"]))
        elif step == "fascists_done" and data["stage"] == "fascists":
            required = get_role_distribution(len(data["participants"])).fascists
            if len(data["fascists"]) != required:
                raise GameError(GameErrorCode.INVALID_ROLES)
            data = await _screen(state, data, stage="winner")
            await reply(callback, "🏆 Кто победил?", keyboards.winner(data["flow"]))
        elif step == "fascists_back" and data["stage"] == "winner":
            data = await _screen(state, data, stage="fascists", winner=None, victory=None)
            await _show_fascists(callback, data)
        elif step == "winner_back" and data["stage"] == "victory":
            data = await _screen(state, data, stage="winner", winner=None, victory=None)
            await reply(callback, "🏆 Кто победил?", keyboards.winner(data["flow"]))
        elif step == "victory_back" and data["stage"] == "review":
            data = await _screen(state, data, stage="victory", victory=None)
            await reply(callback, "Как закончилась игра?", keyboards.victory(Team(data["winner"]), data["flow"]))
        else:
            await reply(callback, ru.GAME_ERRORS["STALE"])
    except Exception as error:
        await _error(callback, error)


@router.callback_query(GameChoice.filter())
async def choice(callback: CallbackQuery, callback_data: GameChoice, state: FSMContext) -> None:
    try:
        if callback_data.kind == "winner":
            data = await _require_data(state, callback_data.flow, "winner")
            team = {"L": Team.LIBERAL, "F": Team.FASCIST}.get(callback_data.code)
            if team is None:
                raise GameError(GameErrorCode.INVALID_VICTORY)
            data = await _screen(state, data, stage="victory", winner=team.value, victory=None)
            await reply(callback, "Как закончилась игра?", keyboards.victory(team, data["flow"]))
        elif callback_data.kind == "victory":
            data = await _require_data(state, callback_data.flow, "victory")
            victory_type = VICTORIES.get(callback_data.code)
            team = Team(data["winner"])
            if victory_type is None:
                raise GameError(GameErrorCode.INVALID_VICTORY)
            validate_victory(team, victory_type)
            data = await _screen(state, data, stage="review", victory=victory_type.value)
            await reply(callback, _review_text(data),
                        keyboards.review(UUID(data["draft_id"]), data["flow"]))
        else:
            await reply(callback, ru.GAME_ERRORS["STALE"])
    except Exception as error:
        await _error(callback, error)


def _review_text(data: dict) -> str:
    participants = tuple(UUID(pid) for pid in data["participants"])
    hitler = UUID(data["hitler"])
    fascists = tuple(UUID(pid) for pid in data["fascists"])
    liberals = assign_liberals(participants, hitler, fascists)
    winner = Team(data["winner"])
    victory_type = VictoryType(data["victory"])
    return ("✅ Проверка · текущий игровой вечер\n"
            f"Игра #{data['sequence']} · игроков: {len(participants)}\n"
            f"👑 Hitler: {_name(data, hitler)}\n"
            f"😈 Fascists: {', '.join(_name(data, pid) for pid in fascists)}\n"
            f"🔵 Liberals: {', '.join(_name(data, pid) for pid in liberals)}\n"
            f"Победа: {winner.value}\nПричина: {VICTORY_LABELS[victory_type]}")


def _saved_text(game: GameResult) -> str:
    return (f"✅ Игра #{game.sequence_number} сохранена\n"
            f"Победа: {game.winner.value}\nПричина: {VICTORY_LABELS[game.victory_type]}\n"
            f"👑 Hitler: {game.hitler.name}\n"
            f"😈 Fascists: {', '.join(p.name for p in game.fascists)}")


@router.callback_query(GameSave.filter())
async def save(callback: CallbackQuery, callback_data: GameSave, state: FSMContext,
               game_service: GameService) -> None:
    try:
        current = await state.get_data()
        # Only the just-saved review can be replayed, never a different active draft.
        replay = (await state.get_state() is None and current.get("stage") == "saved"
                  and current.get("draft_id") == str(callback_data.draft_id)
                  and callback_data.flow.isascii()
                  and secrets.compare_digest(callback_data.flow, current.get("save_flow", "")))
        if replay:
            existing = await game_service.saved_draft(callback.from_user.id, callback_data.draft_id)
            if existing is None:
                raise GameError(GameErrorCode.STALE)
            await reply(callback, _saved_text(existing), keyboards.saved(existing, current["flow"]))
            return
        data = await _require_data(state, callback_data.flow, "review")
        if UUID(data["draft_id"]) != callback_data.draft_id:
            raise GameError(GameErrorCode.STALE)
        game, _ = await game_service.create_game(
            callback.from_user.id, UUID(data["session_id"]), callback_data.draft_id,
            tuple(UUID(pid) for pid in data["participants"]), UUID(data["hitler"]),
            tuple(UUID(pid) for pid in data["fascists"]), Team(data["winner"]),
            VictoryType(data["victory"]),
        )
        # Keep a small receipt, not an unfinished wizard, for duplicate Save and next-game UX.
        await state.set_state(None)
        receipt = await _screen(state, {}, stage="saved", game_id=str(game.id),
                                draft_id=str(callback_data.draft_id), save_flow=callback_data.flow)
        await reply(callback, _saved_text(game), keyboards.saved(game, receipt["flow"]))
    except Exception as error:
        await _error(callback, error)


@router.callback_query(GameAfter.filter())
async def after(callback: CallbackQuery, callback_data: GameAfter, state: FSMContext,
                game_service: GameService, session_service: SessionService) -> None:
    try:
        data = await state.get_data()
        action = callback_data.action
        allowed = {"saved": {"h", "m", "n"}, "next_roster": {"h", "s", "c"}}
        if (await state.get_state() is not None or data.get("game_id") != str(callback_data.game_id)
                or not callback_data.flow.isascii()
                or not secrets.compare_digest(callback_data.flow, data.get("flow", ""))
                or action not in allowed.get(data.get("stage"), set())):
            raise GameError(GameErrorCode.STALE)
        game = await game_service.game(callback.from_user.id, callback_data.game_id)
        if action == "h":
            view = await session_service.view(callback.from_user.id, game.session_id)
            await state.clear()
            await reply(callback, ru.session_message(view), session_view(view))
        elif action == "m":
            await reply(callback, "Добавление моментов будет реализовано на следующем этапе.",
                        keyboards.saved(game, data["flow"]))
        elif action == "n":
            await game_service.setup(callback.from_user.id, game.session_id)
            data = await _screen(state, data, stage="next_roster")
            await reply(callback, "Состав следующей партии тот же?",
                        keyboards.next_roster(game, data["flow"]))
        elif action == "s":
            await begin_wizard(callback, state, game_service, game.session_id,
                               tuple(p.id for p in game.participants))
        elif action == "c":
            view = await session_service.view(callback.from_user.id, game.session_id)
            if view.status is not SessionStatus.ACTIVE or not view.can_manage:
                raise GameError(GameErrorCode.NOT_STATISTICIAN)
            await state.clear()
            await reply(callback, "Измените текущий состав вечера, затем нажмите «Записать игру».",
                        roster_actions(game.session_id))
    except Exception as error:
        await _error(callback, error)


# Old payload versions and malformed current payloads fail closed, including after restart.
@router.callback_query(F.data.startswith(("gp:", "ga:", "gc:", "gs:", "gafter:",
                                         "gp2:", "ga2:", "gc2:", "gs2:", "gf2:")))
async def legacy_game_callback(callback: CallbackQuery) -> None:
    await reply(callback, ru.GAME_ERRORS["STALE"])
