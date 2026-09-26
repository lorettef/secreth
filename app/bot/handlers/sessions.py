"""Telegram controls for the active evening."""

import logging
from uuid import UUID

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery

from app.bot.callbacks import SessionAction, SessionPick, SessionStart, SessionStep
from app.bot.handlers.common import reply
from app.bot.keyboards import sessions as keyboards
from app.locales import ru
from app.services.sessions import SessionError, SessionErrorCode, SessionService
from app.services.games import GameError, GameService
from app.bot.handlers.games import begin_wizard

router = Router()
logger = logging.getLogger(__name__)


class EveningFlow(StatesGroup):
    selecting = State()
    choosing = State()
    confirming = State()


async def _error(callback: CallbackQuery, error: Exception) -> None:
    if isinstance(error, SessionError):
        await reply(callback, ru.SESSION_ERRORS[error.code.value])
    elif isinstance(error, GameError):
        await reply(callback, ru.GAME_ERRORS[error.code.value])
    else:
        logger.exception("Evening callback failed")
        await reply(callback, ru.GENERIC_ERROR)


async def _show(callback: CallbackQuery, service: SessionService, session_id: UUID) -> None:
    view = await service.view(callback.from_user.id, session_id)
    await reply(callback, ru.session_message(view), keyboards.session_view(view))


@router.callback_query(SessionStart.filter())
async def start_evening(callback: CallbackQuery, callback_data: SessionStart,
                        state: FSMContext, session_service: SessionService) -> None:
    try:
        players = await session_service.active_players(callback.from_user.id, callback_data.club_id)
        await state.set_state(EveningFlow.selecting)
        await state.update_data(club_id=str(callback_data.club_id), selected=[], page=0)
        await reply(callback, "Выберите присутствующих игроков. Вечер можно начать и с составом меньше 5 человек.",
                    keyboards.start_choices(players, set(), 0))
    except Exception as error:
        await _error(callback, error)


@router.callback_query(SessionAction.filter())
async def evening_action(callback: CallbackQuery, callback_data: SessionAction,
                         state: FSMContext, session_service: SessionService,
                         game_service: GameService) -> None:
    try:
        action = callback_data.action
        session_id = callback_data.session_id
        if action == "view":
            await state.clear()
            await _show(callback, session_service, session_id)
        elif action == "record":
            await begin_wizard(callback, state, game_service, session_id)
        elif action in ("roster", "summary"):
            view = await session_service.view(callback.from_user.id, session_id)
            if view.status.value != "ACTIVE":
                raise SessionError(SessionErrorCode.SESSION_FINISHED)
            if not view.can_manage:
                raise SessionError(SessionErrorCode.NOT_STATISTICIAN)
            if action == "roster":
                await reply(callback, ru.session_message(view), keyboards.roster_actions(session_id))
            elif action == "summary":
                await reply(callback, ru.session_message(view), keyboards.session_view(view))
        elif action in ("add", "remove", "transfer"):
            players = await session_service.roster_choices(callback.from_user.id, session_id, action)
            if not players:
                await reply(callback, ru.NO_ROSTER_CHOICES)
                return
            await state.set_state(EveningFlow.choosing)
            await state.update_data(session_id=str(session_id), mode=action, page=0)
            title = {"add": "Кто пришёл?", "remove": "Кто ушёл?", "transfer": "Кому передать ведение?"}[action]
            await reply(callback, title, keyboards.player_choices(players, 0))
        elif action == "finish":
            view = await session_service.view(callback.from_user.id, session_id)
            if view.status.value != "ACTIVE":
                raise SessionError(SessionErrorCode.SESSION_FINISHED)
            if not view.can_manage:
                raise SessionError(SessionErrorCode.NOT_STATISTICIAN)
            await state.set_state(EveningFlow.confirming)
            await state.update_data(session_id=str(session_id), mode="finish")
            await reply(callback, "Завершить игровой вечер?", keyboards.confirm("finish"))
        else:
            await reply(callback, ru.INVALID_ACTION)
    except Exception as error:
        await _error(callback, error)


@router.callback_query(SessionPick.filter())
async def pick_player(callback: CallbackQuery, callback_data: SessionPick,
                      state: FSMContext, session_service: SessionService) -> None:
    try:
        current = await state.get_state()
        data = await state.get_data()
        if current == EveningFlow.selecting.state:
            club_id = UUID(data["club_id"])
            players = await session_service.active_players(callback.from_user.id, club_id)
            if callback_data.player_id not in {p.id for p in players}:
                raise SessionError(SessionErrorCode.INVALID_SELECTION)
            selected = {UUID(value) for value in data["selected"]}
            if callback_data.player_id in selected:
                selected.remove(callback_data.player_id)
            else:
                selected.add(callback_data.player_id)
            await state.update_data(selected=[str(value) for value in selected])
            await reply(callback, f"Присутствуют: {len(selected)}. Выберите остальных или начните вечер.",
                        keyboards.start_choices(players, selected, data["page"]))
        elif current == EveningFlow.choosing.state:
            session_id = UUID(data["session_id"])
            mode = data["mode"]
            players = await session_service.roster_choices(callback.from_user.id, session_id, mode)
            if callback_data.player_id not in {p.id for p in players}:
                raise SessionError(SessionErrorCode.PLAYER_NOT_AVAILABLE)
            if mode == "transfer":
                chosen = next(p for p in players if p.id == callback_data.player_id)
                await state.set_state(EveningFlow.confirming)
                await state.update_data(player_id=str(chosen.id))
                await reply(callback, f"Передать ведение игроку {chosen.name}?", keyboards.confirm("transfer"))
            else:
                if mode == "add":
                    view = await session_service.add_player(callback.from_user.id, session_id, callback_data.player_id)
                else:
                    view = await session_service.remove_player(callback.from_user.id, session_id, callback_data.player_id)
                await state.clear()
                await reply(callback, ru.session_message(view), keyboards.session_view(view))
        else:
            await reply(callback, ru.SESSION_STALE)
    except Exception as error:
        await _error(callback, error)


@router.callback_query(SessionStep.filter())
async def step(callback: CallbackQuery, callback_data: SessionStep,
               state: FSMContext, session_service: SessionService) -> None:
    try:
        action = callback_data.action
        current = await state.get_state()
        data = await state.get_data()
        if action == "cancel":
            if current not in (EveningFlow.selecting.state, EveningFlow.choosing.state,
                               EveningFlow.confirming.state):
                await reply(callback, ru.SESSION_STALE)
                return
            await state.clear()
            await reply(callback, "Действие отменено. Откройте /start для главного меню.")
        elif current == EveningFlow.selecting.state:
            club_id = UUID(data["club_id"])
            if action == "create":
                view, created = await session_service.start(
                    callback.from_user.id, club_id, tuple(UUID(value) for value in data["selected"]))
                await state.clear()
                await reply(callback, ("Игровой вечер начался.\n" if created else "Вечер уже начат.\n")
                            + ru.session_message(view), keyboards.session_view(view))
            elif action in ("next", "prev"):
                players = await session_service.active_players(callback.from_user.id, club_id)
                page = max(0, min((len(players) - 1) // 10, data["page"] + (1 if action == "next" else -1)))
                await state.update_data(page=page)
                await reply(callback, "Выберите присутствующих игроков:", keyboards.start_choices(
                    players, {UUID(value) for value in data["selected"]}, page))
            else:
                await reply(callback, ru.INVALID_ACTION)
        elif current == EveningFlow.choosing.state and action in ("next", "prev"):
            players = await session_service.roster_choices(callback.from_user.id, UUID(data["session_id"]), data["mode"])
            page = max(0, min((len(players) - 1) // 10, data["page"] + (1 if action == "next" else -1)))
            await state.update_data(page=page)
            await reply(callback, "Выберите игрока:", keyboards.player_choices(players, page))
        elif current == EveningFlow.confirming.state and action == data.get("mode"):
            session_id = UUID(data["session_id"])
            if action == "finish":
                view = await session_service.finish(callback.from_user.id, session_id)
            else:
                view = await session_service.transfer(callback.from_user.id, session_id, UUID(data["player_id"]))
            await state.clear()
            await reply(callback, ru.session_message(view), keyboards.session_view(view))
        else:
            await reply(callback, ru.SESSION_STALE)
    except Exception as error:
        await _error(callback, error)
