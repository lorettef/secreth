"""Optional, screen-bound event wizard; all writes go through EventService."""

import logging
import secrets
from uuid import UUID, uuid4

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, ForceReply, Message

from app.bot.callbacks import EventAction, EventAfter, EventChoice, EventPick, EventSave, EventStart, GameCard
from app.bot.keyboards import events as keyboards
from app.bot.keyboards.sessions import session_view
from app.domain.enums import EventType, RuinReason
from app.domain.events import EventValidationError, normalize_comment, validate_event
from app.locales import ru
from app.services.events import EventError, EventErrorCode, EventService
from app.services.games import GameError, GamePerson, GameService
from app.services.sessions import SessionError, SessionService

router = Router()
logger = logging.getLogger(__name__)


class EventFlow(StatesGroup):
    active = State()


async def _reply(callback: CallbackQuery, text: str, keyboard=None) -> None:
    # Explicit plain text: names/comments cannot inject HTML even with bot-wide defaults.
    if isinstance(callback.message, Message):
        await callback.answer()
        await callback.message.answer(text, reply_markup=keyboard, parse_mode=None)
    else:
        await callback.answer(text, show_alert=True)


async def _error(callback: CallbackQuery, error: Exception) -> None:
    if isinstance(error, EventError):
        text = ru.EVENT_ERRORS[error.code.value]
    elif isinstance(error, EventValidationError):
        text = ru.EVENT_ERRORS["INVALID_EVENT"]
    elif isinstance(error, GameError):
        text = ru.GAME_ERRORS[error.code.value]
    elif isinstance(error, SessionError):
        text = ru.SESSION_ERRORS[error.code.value]
    else:
        logger.exception("Event wizard failed")
        text = ru.GENERIC_ERROR
    await _reply(callback, text)


async def begin_event(callback: CallbackQuery, state: FSMContext,
                      event_service: EventService, game_id: UUID) -> None:
    if await state.get_state() is not None:
        raise EventError(EventErrorCode.STALE)
    setup = await event_service.setup(callback.from_user.id, game_id)
    if not setup.can_create:
        raise EventError(EventErrorCode.NO_ACCESS)
    await state.set_state(EventFlow.active)
    await _show(callback, state, {
        "game_id": str(game_id), "session_id": str(setup.session_id), "draft_id": str(uuid4()),
        "stage": "type", "participants": [(str(p.id), p.name) for p in setup.participants],
        "event_type": None, "player_id": None, "ruin_reason": None,
        "self_admitted": False, "comment": None,
    })


async def begin_edit(callback, state, event, setup, revision):
    """Reuse the screen-bound event wizard; Save calls the existing update service."""
    if await state.get_state() is not None:
        raise EventError(EventErrorCode.STALE)
    await state.set_state(EventFlow.active)
    await _show(callback, state, {
        "game_id": str(event.game_id), "session_id": str(setup.session_id), "draft_id": str(event.id),
        "edit_id": str(event.id), "revision": revision, "stage": "type",
        "participants": [(str(p.id), p.name) for p in setup.participants],
        "event_type": event.event_type.value, "player_id": str(event.player_id),
        "ruin_reason": event.ruin_reason.value if event.ruin_reason else None,
        "self_admitted": event.self_admitted, "comment": event.comment,
        "original_comment": event.comment,
    })


async def _require(state: FSMContext, flow: str, stage: str | None = None) -> dict:
    data = await state.get_data()
    if (await state.get_state() != EventFlow.active.state or not data.get("draft_id")
            or not flow or not flow.isascii() or not secrets.compare_digest(flow, data.get("flow", ""))
            or (stage is not None and data.get("stage") != stage)):
        raise EventError(EventErrorCode.STALE)
    return data


def _people(data: dict) -> tuple[GamePerson, ...]:
    return tuple(GamePerson(UUID(pid), name) for pid, name in data["participants"])


def _card(data: dict) -> str:
    player = next(p for p in _people(data) if str(p.id) == data["player_id"])
    fields = validate_event(player.id, [p.id for p in _people(data)], EventType(data["event_type"]),
                            RuinReason(data["ruin_reason"]) if data["ruin_reason"] else None,
                            data["self_admitted"], data["comment"])
    return ru.event_card(player.name, fields.event_type, fields.ruin_reason,
                         fields.self_admitted, fields.comment)


async def _show(callback: CallbackQuery, state: FSMContext, data: dict, **changes) -> None:
    current = {**data, **changes, "flow": secrets.token_urlsafe(12), "comment_prompt_id": None}
    stage, flow = current["stage"], current["flow"]
    if stage == "type":
        text, keyboard = ru.EVENT_TYPE_PROMPT, keyboards.choices(ru.EVENT_TYPE_LABELS, "t", flow, back=False)
    elif stage == "player":
        text, keyboard = ru.EVENT_PLAYER_PROMPT, keyboards.players(_people(current), flow)
    elif stage == "reason":
        text, keyboard = ru.EVENT_REASON_PROMPT, keyboards.choices(ru.RUIN_REASON_LABELS, "r", flow)
    elif stage == "admitted":
        text = ru.EVENT_ADMITTED_PROMPT
        keyboard = keyboards.choices({"y": ru.EVENT_YES, "n": ru.EVENT_NO}, "a", flow)
    elif stage == "comment":
        text, keyboard = ru.EVENT_COMMENT_PROMPT, keyboards.comment(flow, allow_clear=bool(current.get("edit_id")))
    else:
        text, keyboard = ru.EVENT_REVIEW + "\n\n" + _card(current), keyboards.review(UUID(current["draft_id"]), flow)
    await state.set_data(current)
    await _reply(callback, text, keyboard)
    if stage == "comment" and isinstance(callback.message, Message):
        prompt = await callback.message.answer(ru.EVENT_COMMENT_REPLY, parse_mode=None,
                                               reply_markup=ForceReply(selective=True))
        await state.update_data(comment_prompt_id=prompt.message_id)


@router.callback_query(EventStart.filter())
async def start_event(callback: CallbackQuery, callback_data: EventStart, state: FSMContext,
                      event_service: EventService) -> None:
    try:
        await begin_event(callback, state, event_service, callback_data.game_id)
    except Exception as error:
        await _error(callback, error)


@router.callback_query(EventChoice.filter())
async def choose(callback: CallbackQuery, callback_data: EventChoice, state: FSMContext) -> None:
    try:
        stage = {"t": "type", "r": "reason", "a": "admitted"}.get(callback_data.kind)
        if stage is None:
            raise EventError(EventErrorCode.STALE)
        data = await _require(state, callback_data.flow, stage)
        if stage == "type":
            event_type = EventType(callback_data.code)
            await _show(callback, state, data, stage="player", event_type=event_type.value,
                        player_id=None, ruin_reason=None, self_admitted=False, comment=None)
        elif stage == "reason":
            reason = RuinReason(callback_data.code)
            await _show(callback, state, data, stage="admitted", ruin_reason=reason.value)
        else:
            if callback_data.code not in ("y", "n"):
                raise EventError(EventErrorCode.INVALID_EVENT)
            await _show(callback, state, data, stage="comment", self_admitted=callback_data.code == "y")
    except ValueError as error:
        await _error(callback, error if isinstance(error, EventError) else EventError(EventErrorCode.INVALID_EVENT))
    except Exception as error:
        await _error(callback, error)


@router.callback_query(EventPick.filter())
async def pick_player(callback: CallbackQuery, callback_data: EventPick, state: FSMContext) -> None:
    try:
        data = await _require(state, callback_data.flow, "player")
        if callback_data.player_id not in {p.id for p in _people(data)}:
            raise EventError(EventErrorCode.INVALID_EVENT)
        await _show(callback, state, data, player_id=str(callback_data.player_id),
                    stage="reason" if data["event_type"] == EventType.RUIN.value else "comment")
    except Exception as error:
        await _error(callback, error)


async def show_game(callback: CallbackQuery, state: FSMContext, game_id: UUID,
                    game_service: GameService, event_service: EventService, history_service=None) -> None:
    if history_service is not None:
        from app.bot.handlers.history import show_card
        await show_card(callback, history_service, game_id)
        await state.clear()
        return
    from app.bot.handlers.games import _saved_text
    game = await game_service.game(callback.from_user.id, game_id)
    setup = await event_service.setup(callback.from_user.id, game_id)
    await state.clear()
    await _reply(callback, _saved_text(game), keyboards.game_card(game.id, game.session_id, setup.can_create))


@router.callback_query(GameCard.filter())
async def game_card(callback: CallbackQuery, callback_data: GameCard, state: FSMContext,
                    game_service: GameService, event_service: EventService) -> None:
    try:
        if await state.get_state() is not None:
            raise EventError(EventErrorCode.STALE)
        await show_game(callback, state, callback_data.game_id, game_service, event_service)
    except Exception as error:
        await _error(callback, error)


@router.callback_query(EventAction.filter())
async def action(callback: CallbackQuery, callback_data: EventAction, state: FSMContext,
                 game_service: GameService, event_service: EventService, history_service=None) -> None:
    try:
        data = await _require(state, callback_data.flow)
        if callback_data.action == "cancel":
            await show_game(callback, state, UUID(data["game_id"]), game_service, event_service, history_service)
            await _reply(callback, ru.EVENT_CANCELLED)
        elif callback_data.action == "skip" and data["stage"] == "comment":
            await _show(callback, state, data, stage="review", comment=data.get("original_comment") if data.get("edit_id") else None)
        elif callback_data.action == "clear" and data["stage"] == "comment" and data.get("edit_id"):
            await _show(callback, state, data, stage="review", comment=None)
        elif callback_data.action == "back":
            target = {"player": "type", "reason": "player", "admitted": "reason", "review": "comment"}.get(data["stage"])
            if data["stage"] == "comment":
                target = "admitted" if data["event_type"] == EventType.RUIN.value else "player"
            if target is None:
                raise EventError(EventErrorCode.STALE)
            await _show(callback, state, data, stage=target)
        else:
            raise EventError(EventErrorCode.STALE)
    except Exception as error:
        await _error(callback, error)


@router.message(EventFlow.active, F.text)
async def comment(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if (await state.get_state() != EventFlow.active.state
            or data.get("stage") != "comment" or not data.get("comment_prompt_id")
            or message.reply_to_message is None
            or message.reply_to_message.message_id != data["comment_prompt_id"]):
        await message.answer(ru.EVENT_COMMENT_STALE, parse_mode=None)
        return
    try:
        text = normalize_comment(message.text)
        if text is None:
            raise EventValidationError("Empty comment")
    except EventValidationError:
        await message.answer(ru.EVENT_COMMENT_INVALID, parse_mode=None)
        return
    # Message handling shares the dispatcher's per-user event isolation with callbacks.
    current = {**data, "stage": "review", "comment": text,
               "flow": secrets.token_urlsafe(12), "comment_prompt_id": None}
    await state.set_data(current)
    await message.answer(ru.EVENT_REVIEW + "\n\n" + _card(current), parse_mode=None,
                         reply_markup=keyboards.review(UUID(current["draft_id"]), current["flow"]))


@router.callback_query(EventSave.filter())
async def save(callback: CallbackQuery, callback_data: EventSave, state: FSMContext,
               event_service: EventService) -> None:
    try:
        data = await state.get_data()
        replay = (await state.get_state() is None and data.get("stage") == "event_saved"
                  and data.get("draft_id") == str(callback_data.draft_id)
                  and callback_data.flow.isascii()
                  and secrets.compare_digest(callback_data.flow, data.get("save_flow", "")))
        if replay:
            event = await event_service.event(callback.from_user.id, callback_data.draft_id)
            flow = data["flow"]
        else:
            data = await _require(state, callback_data.flow, "review")
            if data["draft_id"] != str(callback_data.draft_id):
                raise EventError(EventErrorCode.STALE)
            reason = RuinReason(data["ruin_reason"]) if data["ruin_reason"] else None
            if data.get("edit_id"):
                event = await event_service.update_event(callback.from_user.id, UUID(data["edit_id"]),
                    player_id=UUID(data["player_id"]), event_type=EventType(data["event_type"]),
                    ruin_reason=reason, self_admitted=data["self_admitted"], comment=data["comment"],
                    expected_revision=data["revision"])
            else:
                event, _ = await event_service.create_event(
                    callback.from_user.id, UUID(data["game_id"]), callback_data.draft_id,
                    UUID(data["player_id"]), EventType(data["event_type"]), reason,
                    data["self_admitted"], data["comment"])
            flow = secrets.token_urlsafe(12)
            await state.set_state(None)
            await state.set_data({"stage": "event_saved", "draft_id": str(event.id),
                                  "flow": flow, "save_flow": callback_data.flow})
        text = ru.EVENT_SAVED + "\n\n" + ru.event_card(event.player_name, event.event_type,
            event.ruin_reason, event.self_admitted, event.comment)
        await _reply(callback, text, keyboards.saved(event.id, flow))
    except Exception as error:
        await _error(callback, error)


@router.callback_query(EventAfter.filter())
async def after(callback: CallbackQuery, callback_data: EventAfter, state: FSMContext,
                event_service: EventService, game_service: GameService,
                session_service: SessionService, history_service=None) -> None:
    try:
        data = await state.get_data()
        if (await state.get_state() is not None or data.get("stage") != "event_saved"
                or data.get("draft_id") != str(callback_data.event_id)
                or not callback_data.flow or not callback_data.flow.isascii()
                or not secrets.compare_digest(callback_data.flow, data.get("flow", ""))):
            raise EventError(EventErrorCode.STALE)
        event = await event_service.event(callback.from_user.id, callback_data.event_id)
        if callback_data.action == "m":
            await begin_event(callback, state, event_service, event.game_id)
        elif callback_data.action == "g":
            await show_game(callback, state, event.game_id, game_service, event_service, history_service)
        elif callback_data.action == "h":
            setup = await event_service.setup(callback.from_user.id, event.game_id)
            view = await session_service.view(callback.from_user.id, setup.session_id)
            await state.clear()
            await _reply(callback, ru.session_message(view), session_view(view))
        else:
            raise EventError(EventErrorCode.STALE)
    except Exception as error:
        await _error(callback, error)


@router.callback_query(F.data.startswith(("ec:", "ep:", "ea:", "es:", "ef:", "estart:", "gcard:")))
async def malformed(callback: CallbackQuery) -> None:
    await _reply(callback, ru.EVENT_ERRORS["STALE"])
