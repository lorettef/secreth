"""Telegram controls bound to one Session FSM screen or persisted recovery request."""

import logging
import secrets
from uuid import UUID, uuid4

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery
from aiogram.utils.deep_linking import create_start_link

from app.bot.callbacks import RecoveryAction, SessionAction, SessionPick, SessionStart, SessionStep
from app.bot.handlers.common import reply
from app.bot.handlers.games import begin_wizard
from app.bot.keyboards import sessions as keyboards
from app.domain.enums import RecoveryStatus, SessionStatus
from app.locales import ru
from app.services.games import GameError, GameService
from app.services.sessions import SessionError, SessionErrorCode, SessionService

router = Router()
logger = logging.getLogger(__name__)


class EveningFlow(StatesGroup):
    menu = State()
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


async def _idle(state: FSMContext) -> None:
    # An old entry/menu button must not replace or clear an unrelated current draft.
    if await state.get_state() is not None:
        raise SessionError(SessionErrorCode.STALE)


async def _screen(state: FSMContext, stage: State, **data) -> str:
    flow = secrets.token_urlsafe(12)
    await state.set_state(stage)
    await state.set_data({**data, "flow": flow})
    return flow


async def _require_flow(state: FSMContext, flow: str) -> tuple[str, dict]:
    stage = await state.get_state()
    data = await state.get_data()
    if stage not in (EveningFlow.menu.state, EveningFlow.selecting.state,
                     EveningFlow.choosing.state, EveningFlow.confirming.state):
        raise SessionError(SessionErrorCode.STALE)
    if not flow or not flow.isascii() or not secrets.compare_digest(data.get("flow", ""), flow):
        raise SessionError(SessionErrorCode.STALE)
    return stage, data


async def _show(callback: CallbackQuery, service: SessionService, session_id: UUID) -> None:
    view = await service.view(callback.from_user.id, session_id)
    await reply(callback, ru.session_message(view), keyboards.session_view(view))


async def _choose(callback: CallbackQuery, state: FSMContext, service: SessionService,
                  data: dict, mode: str) -> None:
    session_id = UUID(data["session_id"])
    if mode == "recovery":
        players = await service.recovery_choices(callback.from_user.id, session_id)
    else:
        players = await service.roster_choices(callback.from_user.id, session_id, mode)
    if not players:
        await reply(callback, ru.NO_ROSTER_CHOICES)
        return
    flow = await _screen(state, EveningFlow.choosing, **{**data, "mode": mode, "page": 0})
    await reply(callback, ru.SESSION_CHOICE_TITLES[mode], keyboards.player_choices(players, 0, flow))


@router.callback_query(SessionStart.filter())
async def start_evening(callback: CallbackQuery, callback_data: SessionStart,
                        state: FSMContext, session_service: SessionService) -> None:
    try:
        await _idle(state)
        players = await session_service.active_players(callback.from_user.id, callback_data.club_id)
        flow = await _screen(state, EveningFlow.selecting,
                             club_id=str(callback_data.club_id), selected=[], page=0)
        await reply(callback, ru.SESSION_START_PROMPT, keyboards.start_choices(players, set(), 0, flow))
    except Exception as error:
        await _error(callback, error)


@router.callback_query(SessionAction.filter())
async def evening_action(callback: CallbackQuery, callback_data: SessionAction,
                         state: FSMContext, session_service: SessionService,
                         game_service: GameService) -> None:
    try:
        await _idle(state)
        action = callback_data.action
        session_id = callback_data.session_id
        if action == "view":
            await _show(callback, session_service, session_id)
            return
        if action == "record":
            await begin_wizard(callback, state, game_service, session_id)
            return
        view = await session_service.view(callback.from_user.id, session_id)
        if view.status is not SessionStatus.ACTIVE:
            raise SessionError(SessionErrorCode.SESSION_FINISHED)
        data = {"session_id": str(session_id), "expected_statistician_id": str(view.statistician_player_id)}
        if action == "help":
            if not view.can_recover:
                raise SessionError(SessionErrorCode.NO_ACCESS)
            flow = await _screen(state, EveningFlow.menu, **data, mode="help")
            await reply(callback, ru.RECOVERY_BUTTON + "\n" + ru.RECOVERY_HELP, keyboards.recovery_help(flow))
            return
        if not view.can_manage:
            raise SessionError(SessionErrorCode.NOT_STATISTICIAN)
        if action == "roster":
            flow = await _screen(state, EveningFlow.menu, **data, mode="roster")
            await reply(callback, ru.session_message(view), keyboards.flow_roster_actions(flow))
        elif action == "summary":
            await reply(callback, ru.session_message(view), keyboards.session_view(view))
        elif action in ("add", "remove", "transfer"):
            await _choose(callback, state, session_service, data, action)
        elif action == "finish":
            flow = await _screen(state, EveningFlow.confirming, **data, mode="finish")
            await reply(callback, ru.SESSION_FINISH_PROMPT, keyboards.confirm("finish", flow))
        else:
            await reply(callback, ru.INVALID_ACTION)
    except Exception as error:
        await _error(callback, error)


@router.callback_query(SessionPick.filter())
async def pick_player(callback: CallbackQuery, callback_data: SessionPick,
                      state: FSMContext, session_service: SessionService) -> None:
    try:
        current, data = await _require_flow(state, callback_data.flow)
        if current == EveningFlow.selecting.state:
            players = await session_service.active_players(callback.from_user.id, UUID(data["club_id"]))
            if callback_data.player_id not in {p.id for p in players}:
                raise SessionError(SessionErrorCode.INVALID_SELECTION)
            selected = {UUID(value) for value in data["selected"]}
            if callback_data.player_id in selected:
                selected.remove(callback_data.player_id)
            else:
                selected.add(callback_data.player_id)
            flow = await _screen(state, EveningFlow.selecting,
                                 **{**data, "selected": [str(value) for value in selected]})
            await reply(callback, f"Присутствуют: {len(selected)}. {ru.SESSION_SELECT_PROMPT}",
                        keyboards.start_choices(players, selected, data["page"], flow))
        elif current == EveningFlow.choosing.state:
            session_id = UUID(data["session_id"])
            mode = data["mode"]
            if mode == "recovery":
                players = await session_service.recovery_choices(callback.from_user.id, session_id)
            else:
                players = await session_service.roster_choices(callback.from_user.id, session_id, mode)
            if callback_data.player_id not in {p.id for p in players}:
                raise SessionError(SessionErrorCode.PLAYER_NOT_AVAILABLE)
            if mode in ("transfer", "recovery"):
                chosen = next(p for p in players if p.id == callback_data.player_id)
                flow = await _screen(state, EveningFlow.confirming,
                                     **{**data, "player_id": str(chosen.id), "request_id": str(uuid4())})
                await reply(callback, f"Передать ведение игроку {chosen.name}?", keyboards.confirm(mode, flow))
            else:
                operation = session_service.add_player if mode == "add" else session_service.remove_player
                view = await operation(callback.from_user.id, session_id, callback_data.player_id,
                                       expected_statistician_id=UUID(data["expected_statistician_id"]))
                await state.clear()
                await reply(callback, ru.session_message(view), keyboards.session_view(view))
        else:
            raise SessionError(SessionErrorCode.STALE)
    except Exception as error:
        await _error(callback, error)


@router.callback_query(SessionStep.filter())
async def step(callback: CallbackQuery, callback_data: SessionStep,
               state: FSMContext, session_service: SessionService) -> None:
    try:
        current, data = await _require_flow(state, callback_data.flow)
        action = callback_data.action
        if action == "cancel":
            await state.clear()
            await reply(callback, ru.SESSION_CANCELLED)
        elif current == EveningFlow.menu.state:
            if action == "back":
                view = await session_service.view(callback.from_user.id, UUID(data["session_id"]))
                await state.clear()
                await reply(callback, ru.session_message(view), keyboards.session_view(view))
            elif (data["mode"] == "roster" and action in ("add", "remove")) or (
                    data["mode"] == "help" and action == "recovery"):
                await _choose(callback, state, session_service, data, action)
            else:
                raise SessionError(SessionErrorCode.STALE)
        elif current == EveningFlow.selecting.state:
            club_id = UUID(data["club_id"])
            if action == "create":
                view, created = await session_service.start(
                    callback.from_user.id, club_id, tuple(UUID(value) for value in data["selected"]))
                await state.clear()
                await reply(callback, (ru.SESSION_STARTED if created else ru.SESSION_ALREADY_STARTED)
                            + ru.session_message(view), keyboards.session_view(view))
            elif action in ("next", "prev"):
                players = await session_service.active_players(callback.from_user.id, club_id)
                page = max(0, min((len(players) - 1) // 10, data["page"] + (1 if action == "next" else -1)))
                flow = await _screen(state, EveningFlow.selecting, **{**data, "page": page})
                await reply(callback, ru.SESSION_SELECT_PROMPT, keyboards.start_choices(
                    players, {UUID(value) for value in data["selected"]}, page, flow))
            else:
                raise SessionError(SessionErrorCode.STALE)
        elif current == EveningFlow.choosing.state and action in ("next", "prev"):
            session_id = UUID(data["session_id"])
            mode = data["mode"]
            if mode == "recovery":
                players = await session_service.recovery_choices(callback.from_user.id, session_id)
            else:
                players = await session_service.roster_choices(callback.from_user.id, session_id, mode)
            page = max(0, min((len(players) - 1) // 10, data["page"] + (1 if action == "next" else -1)))
            flow = await _screen(state, EveningFlow.choosing, **{**data, "page": page})
            await reply(callback, ru.SESSION_PICK_PROMPT, keyboards.player_choices(players, page, flow))
        elif current == EveningFlow.confirming.state and action == data.get("mode"):
            session_id = UUID(data["session_id"])
            expected = UUID(data["expected_statistician_id"])
            if action == "recovery":
                recovery = await session_service.request_recovery(
                    callback.from_user.id, session_id, UUID(data["player_id"]), UUID(data["request_id"]),
                    expected_statistician_id=expected)
                text = ru.recovery_message(recovery)
                if recovery.status is RecoveryStatus.PENDING:
                    link = await create_start_link(callback.bot, f"recovery_{recovery.id.hex}")
                    text += f"\n{ru.RECOVERY_SHARE}\n{link}"
                await state.clear()
                await reply(callback, text, keyboards.recovery(recovery))
            else:
                if action == "finish":
                    view = await session_service.finish(callback.from_user.id, session_id,
                                                        expected_statistician_id=expected)
                else:
                    view = await session_service.transfer(callback.from_user.id, session_id,
                        UUID(data["player_id"]), expected_statistician_id=expected)
                await state.clear()
                await reply(callback, ru.session_message(view), keyboards.session_view(view))
        else:
            raise SessionError(SessionErrorCode.STALE)
    except Exception as error:
        await _error(callback, error)


@router.callback_query(RecoveryAction.filter())
async def recovery_action(callback: CallbackQuery, callback_data: RecoveryAction,
                          session_service: SessionService) -> None:
    try:
        if callback_data.action == "confirm":
            view = await session_service.confirm_recovery(callback.from_user.id, callback_data.request_id)
        elif callback_data.action == "view":
            view = await session_service.recovery_view(callback.from_user.id, callback_data.request_id)
        else:
            raise SessionError(SessionErrorCode.STALE)
        await reply(callback, ru.recovery_message(view), keyboards.recovery(view))
    except Exception as error:
        await _error(callback, error)


@router.callback_query(F.data.regexp(r"^(?:sp|step|sp2|step2):"))
async def legacy_session_callback(callback: CallbackQuery) -> None:
    # Old payloads and malformed scoped payloads must receive a friendly reply too.
    await reply(callback, ru.SESSION_STALE)
