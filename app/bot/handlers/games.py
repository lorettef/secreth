"""Fast FSM wizard for completed games; no draft rows until Save."""

import logging
import uuid
from uuid import UUID

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery

from app.bot.callbacks import GameAction, GameAfter, GameChoice, GamePick, GameSave
from app.bot.handlers.common import reply
from app.bot.keyboards import games as keyboards
from app.bot.keyboards.sessions import roster_actions, session_view
from app.domain.enums import SessionStatus, Team, VictoryType
from app.domain.roles import assign_liberals, get_role_distribution
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
    else:
        logger.exception("Game wizard failed")
        await reply(callback, ru.GENERIC_ERROR)


async def begin_wizard(callback: CallbackQuery, state: FSMContext, service: GameService,
                       session_id: UUID, same_participants: tuple[UUID, ...] | None = None) -> None:
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
    await state.update_data(**data)
    if stage == "hitler":
        await reply(callback, f"🎲 Игра #{setup.sequence_number}\n👑 Кто был Hitler?\n"
                    "Текущий состав выбран автоматически; его можно изменить.",
                    keyboards.hitler(_selected(data)))
    else:
        await reply(callback, f"🎲 Игра #{setup.sequence_number}\n"
                    f"Присутствуют {len(setup.roster)} игроков. Выберите 5–10 участников партии (0/10).",
                    keyboards.participants(setup.candidates, set(), 0))


async def _require_data(state: FSMContext, stage: str | None = None) -> dict:
    data = await state.get_data()
    if await state.get_state() != GameFlow.active.state or not data.get("draft_id"):
        raise GameError(GameErrorCode.DRAFT_CONFLICT)
    if stage is not None and data.get("stage") != stage:
        raise GameError(GameErrorCode.DRAFT_CONFLICT)
    return data


@router.callback_query(GamePick.filter())
async def pick(callback: CallbackQuery, callback_data: GamePick, state: FSMContext) -> None:
    try:
        data = await _require_data(state, callback_data.step)
        pid = callback_data.player_id
        if callback_data.step == "participants":
            if pid not in {p.id for p in _people(data)}:
                raise GameError(GameErrorCode.INVALID_PARTICIPANTS)
            selected = {UUID(value) for value in data["participants"]}
            if pid in selected:
                selected.remove(pid)
            elif len(selected) < 10:
                selected.add(pid)
            else:
                raise GameError(GameErrorCode.INVALID_PARTICIPANTS)
            await state.update_data(participants=[str(value) for value in selected],
                                    hitler=None, fascists=[], winner=None, victory=None)
            await reply(callback, f"👥 Выберите участников партии — {len(selected)}/10 (минимум 5)",
                        keyboards.participants(_people(data), selected, data["page"]))
        elif callback_data.step == "hitler":
            if pid not in {p.id for p in _selected(data)}:
                raise GameError(GameErrorCode.INVALID_ROLES)
            await state.update_data(stage="fascists", hitler=str(pid), fascists=[],
                                    winner=None, victory=None)
            await _show_fascists(callback, {**data, "hitler": str(pid), "fascists": []})
        elif callback_data.step == "fascists":
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
            await state.update_data(fascists=[str(value) for value in selected],
                                    winner=None, victory=None)
            await _show_fascists(callback, {**data, "fascists": [str(value) for value in selected]})
    except Exception as error:
        await _error(callback, error)


async def _show_fascists(callback: CallbackQuery, data: dict) -> None:
    hitler = UUID(data["hitler"])
    required = get_role_distribution(len(data["participants"])).fascists
    chosen = {UUID(value) for value in data["fascists"]}
    players = tuple(p for p in _selected(data) if p.id != hitler)
    await reply(callback, f"😈 Выберите Fascists — {len(chosen)}/{required}",
                keyboards.fascists(players, chosen, required))


@router.callback_query(GameAction.filter())
async def action(callback: CallbackQuery, callback_data: GameAction, state: FSMContext) -> None:
    try:
        if callback_data.action == "cancel":
            await _require_data(state)
            await state.clear()
            await reply(callback, "Запись партии отменена. Откройте вечер для новой записи.")
            return
        data = await _require_data(state)
        step = callback_data.action
        if step == "participants" and data["stage"] == "hitler":
            await state.update_data(stage="participants", page=0)
            await reply(callback, f"👥 Выберите участников — {len(data['participants'])}/10 (минимум 5)",
                        keyboards.participants(_people(data),
                                               {UUID(pid) for pid in data["participants"]}, 0))
        elif step in ("next", "prev") and data["stage"] == "participants":
            page = max(0, min((len(data["candidates"]) - 1) // 10,
                              data["page"] + (1 if step == "next" else -1)))
            await state.update_data(page=page)
            await reply(callback, f"👥 Выберите участников — {len(data['participants'])}/10 (минимум 5)",
                        keyboards.participants(_people(data),
                                               {UUID(pid) for pid in data["participants"]}, page))
        elif step == "participants_done" and data["stage"] == "participants":
            if not 5 <= len(data["participants"]) <= 10:
                raise GameError(GameErrorCode.INVALID_PARTICIPANTS)
            await state.update_data(stage="hitler", hitler=None, fascists=[], winner=None, victory=None)
            await reply(callback, "👑 Кто был Hitler?", keyboards.hitler(_selected(data)))
        elif step == "hitler_back" and data["stage"] == "fascists":
            await state.update_data(stage="hitler", hitler=None, fascists=[])
            await reply(callback, "👑 Кто был Hitler?", keyboards.hitler(_selected(data)))
        elif step == "fascists_done" and data["stage"] == "fascists":
            required = get_role_distribution(len(data["participants"])).fascists
            if len(data["fascists"]) != required:
                raise GameError(GameErrorCode.INVALID_ROLES)
            await state.update_data(stage="winner")
            await reply(callback, "🏆 Кто победил?", keyboards.winner())
        elif step == "fascists_back" and data["stage"] == "winner":
            await state.update_data(stage="fascists", winner=None, victory=None)
            await _show_fascists(callback, data)
        elif step == "winner_back" and data["stage"] == "victory":
            await state.update_data(stage="winner", winner=None, victory=None)
            await reply(callback, "🏆 Кто победил?", keyboards.winner())
        elif step == "victory_back" and data["stage"] == "review":
            await state.update_data(stage="victory", victory=None)
            await reply(callback, "Как закончилась игра?", keyboards.victory(Team(data["winner"])))
        else:
            await reply(callback, ru.SESSION_STALE)
    except Exception as error:
        await _error(callback, error)


@router.callback_query(GameChoice.filter())
async def choice(callback: CallbackQuery, callback_data: GameChoice, state: FSMContext) -> None:
    try:
        if callback_data.kind == "winner":
            data = await _require_data(state, "winner")
            team = {"L": Team.LIBERAL, "F": Team.FASCIST}.get(callback_data.code)
            if team is None:
                raise GameError(GameErrorCode.INVALID_VICTORY)
            await state.update_data(stage="victory", winner=team.value, victory=None)
            await reply(callback, "Как закончилась игра?", keyboards.victory(team))
        elif callback_data.kind == "victory":
            data = await _require_data(state, "victory")
            victory_type = VICTORIES.get(callback_data.code)
            team = Team(data["winner"])
            if victory_type is None or (team is Team.LIBERAL) != (
                victory_type in (VictoryType.LIBERAL_POLICIES, VictoryType.HITLER_KILLED)):
                raise GameError(GameErrorCode.INVALID_VICTORY)
            await state.update_data(stage="review", victory=victory_type.value)
            await reply(callback, _review_text({**data, "victory": victory_type.value}),
                        keyboards.review(UUID(data["draft_id"])))
        else:
            await reply(callback, ru.SESSION_STALE)
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
        existing = await game_service.saved_draft(callback.from_user.id, callback_data.draft_id)
        if existing is not None:
            current = await state.get_data()
            if current.get("draft_id") == str(callback_data.draft_id):
                await state.clear()
            await reply(callback, _saved_text(existing), keyboards.saved(existing))
            return
        data = await _require_data(state, "review")
        if UUID(data["draft_id"]) != callback_data.draft_id:
            raise GameError(GameErrorCode.DRAFT_CONFLICT)
        game, _ = await game_service.create_game(
            callback.from_user.id, UUID(data["session_id"]), callback_data.draft_id,
            tuple(UUID(pid) for pid in data["participants"]), UUID(data["hitler"]),
            tuple(UUID(pid) for pid in data["fascists"]), Team(data["winner"]),
            VictoryType(data["victory"]),
        )
        await state.clear()
        await reply(callback, _saved_text(game), keyboards.saved(game))
    except Exception as error:
        await _error(callback, error)


@router.callback_query(GameAfter.filter())
async def after(callback: CallbackQuery, callback_data: GameAfter, state: FSMContext,
                game_service: GameService, session_service: SessionService) -> None:
    try:
        game = await game_service.game(callback.from_user.id, callback_data.game_id)
        action = callback_data.action
        if action == "home":
            view = await session_service.view(callback.from_user.id, game.session_id)
            await reply(callback, ru.session_message(view), session_view(view))
        elif action == "moment":
            await reply(callback, "Добавление моментов будет реализовано на следующем этапе.",
                        keyboards.saved(game))
        elif action == "next":
            await game_service.setup(callback.from_user.id, game.session_id)
            await reply(callback, "Состав следующей партии тот же?", keyboards.next_roster(game))
        elif action == "same":
            await begin_wizard(callback, state, game_service, game.session_id,
                               tuple(p.id for p in game.participants))
        elif action == "changed":
            view = await session_service.view(callback.from_user.id, game.session_id)
            if view.status is not SessionStatus.ACTIVE or not view.can_manage:
                raise GameError(GameErrorCode.NOT_STATISTICIAN)
            await reply(callback, "Измените текущий состав вечера, затем нажмите «Записать игру».",
                        roster_actions(game.session_id))
        else:
            await reply(callback, ru.SESSION_STALE)
    except Exception as error:
        await _error(callback, error)
