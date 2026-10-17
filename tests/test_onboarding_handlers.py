"""Telegram rendering and stale callback behavior without network calls."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from aiogram import Bot
from aiogram.filters import CommandObject
from aiogram.types import CallbackQuery, Chat, Message, User

from app.bot.callbacks import ClubAction, GameAfter, GamePick, GameSave, JoinInvite, ReviewLink, SessionAction, SessionStart
from app.bot.handlers.admin import review_pending
from app.bot.handlers.membership import club_action, join_club
from app.bot.handlers.start import start
from app.domain.enums import MembershipRole
from app.locales import ru
from app.services.membership import (
    ClubView,
    InviteView,
    MembershipError,
    MembershipErrorCode,
)
from app.services.sessions import SessionMenuState


@pytest.fixture
def session_service() -> AsyncMock:
    service = AsyncMock()
    service.menu_state.return_value = SessionMenuState(None, False)
    return service


@pytest.fixture
def message(monkeypatch: pytest.MonkeyPatch) -> tuple[Message, AsyncMock]:
    answer = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer)
    user = User(id=200, is_bot=False, first_name="Max", username="max")
    telegram_message = Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=Chat(id=200, type="private"),
        from_user=user,
        text="/start",
    )
    return telegram_message, answer


@pytest.fixture
def callback(monkeypatch: pytest.MonkeyPatch, message: tuple[Message, AsyncMock]) -> CallbackQuery:
    monkeypatch.setattr(CallbackQuery, "answer", AsyncMock())
    return CallbackQuery(
        id="callback-1",
        from_user=message[0].from_user,
        chat_instance="chat-1",
        message=message[0],
        data="unused",
    )


@pytest.mark.asyncio
async def test_start_invite_shows_club_and_join_button(message: tuple[Message, AsyncMock], session_service: AsyncMock) -> None:
    telegram_message, answer = message
    invite_id = UUID(int=1)
    service = AsyncMock()
    service.preview_invite.return_value = InviteView(invite_id, "Saturday Club")

    await start(
        telegram_message,
        CommandObject(command="start", args="invite_secret-token"),
        service,
        session_service,
    )

    service.register_telegram_user.assert_awaited_once()
    service.preview_invite.assert_awaited_once_with("secret-token")
    text = answer.await_args.args[0]
    keyboard = answer.await_args.kwargs["reply_markup"]
    assert "Saturday Club" in text
    assert keyboard.inline_keyboard[0][0].callback_data == JoinInvite(invite_id=invite_id).pack()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,has_admin_button",
    [
        (MembershipRole.PLAYER, False),
        (MembershipRole.ADMIN, True),
        (MembershipRole.OWNER, True),
    ],
)
async def test_start_existing_member_shows_role_menu(
    message: tuple[Message, AsyncMock], role: MembershipRole, has_admin_button: bool,
    session_service: AsyncMock,
) -> None:
    telegram_message, answer = message
    service = AsyncMock()
    service.list_clubs.return_value = (ClubView(UUID(int=2), "Saturday Club", role, "Max", False),)

    await start(telegram_message, CommandObject(command="start"), service, session_service)

    keyboard = answer.await_args.kwargs["reply_markup"]
    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    assert ru.PROFILE_BUTTON in labels
    assert (ru.ADMIN_BUTTON in labels) is has_admin_button


@pytest.mark.asyncio
async def test_invalid_invite_has_friendly_message(message: tuple[Message, AsyncMock], session_service: AsyncMock) -> None:
    telegram_message, answer = message
    service = AsyncMock()
    service.preview_invite.side_effect = MembershipError(MembershipErrorCode.INVITE_INVALID)

    await start(telegram_message, CommandObject(command="start", args="invite_bad"), service, session_service)

    assert answer.await_args.args[0] == ru.ERRORS["INVITE_INVALID"]


@pytest.mark.asyncio
async def test_stale_join_button_rechecks_invite(
    callback: CallbackQuery, message: tuple[Message, AsyncMock], session_service: AsyncMock
) -> None:
    service = AsyncMock()
    service.join_invite.side_effect = MembershipError(MembershipErrorCode.INVITE_EXPIRED)

    await join_club(callback, JoinInvite(invite_id=UUID(int=3)), service, session_service)

    assert message[1].await_args.args[0] == ru.ERRORS["INVITE_EXPIRED"]


@pytest.mark.asyncio
async def test_inaccessible_callback_message_uses_alert(callback: CallbackQuery, session_service: AsyncMock) -> None:
    callback = callback.model_copy(update={"message": None})
    service = AsyncMock()
    service.join_invite.side_effect = MembershipError(MembershipErrorCode.INVITE_EXPIRED)

    await join_club(callback, JoinInvite(invite_id=UUID(int=3)), service, session_service)

    callback.answer.assert_awaited_once_with(ru.ERRORS["INVITE_EXPIRED"], show_alert=True)


@pytest.mark.asyncio
async def test_unauthorized_review_callback_is_safe(
    callback: CallbackQuery, message: tuple[Message, AsyncMock]
) -> None:
    service = AsyncMock()
    service.get_pending_link.side_effect = MembershipError(MembershipErrorCode.NOT_ADMIN)

    await review_pending(callback, ReviewLink(request_id=UUID(int=4), action="approve"), service)

    service.review_player_link.assert_not_awaited()
    assert message[1].await_args.args[0] == ru.ERRORS["NOT_ADMIN"]


def test_callback_payloads_fit_telegram_limit() -> None:
    identifier = UUID(int=5)
    assert JoinInvite.unpack(JoinInvite(invite_id=identifier).pack()).invite_id == identifier
    assert len(ClubAction(club_id=identifier, action="invite").pack().encode()) <= 64
    assert len(ReviewLink(request_id=identifier, action="approve").pack().encode()) <= 64
    assert len(SessionAction(session_id=identifier, action="transfer").pack().encode()) <= 64
    assert len(SessionStart(club_id=identifier).pack().encode()) <= 64
    flow = "x" * 16
    assert len(GamePick(flow=flow, step="p", player_id=identifier).pack().encode()) <= 64
    assert len(GameSave(flow=flow, draft_id=identifier).pack().encode()) <= 64
    assert len(GameAfter(flow=flow, game_id=identifier, action="c").pack().encode()) <= 64


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "expected_uses", "has_expiry"),
    [("invite", None, False), ("invite1", 1, False), ("invite7", None, True)],
)
async def test_admin_invite_button_returns_deep_link(
    callback: CallbackQuery,
    message: tuple[Message, AsyncMock],
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    expected_uses: int | None,
    has_expiry: bool,
    session_service: AsyncMock,
) -> None:
    service = AsyncMock()
    service.create_invite.return_value = "random-token"
    deep_link = AsyncMock(return_value="https://t.me/example_bot?start=invite_random-token")
    monkeypatch.setattr("app.bot.handlers.membership.create_start_link", deep_link)
    bot = Bot(token="123456:example")
    callback.as_(bot)
    try:
        await club_action(callback, ClubAction(club_id=UUID(int=6), action=action), service, session_service)
    finally:
        await bot.session.close()

    assert service.create_invite.await_args.args == (200, UUID(int=6))
    assert service.create_invite.await_args.kwargs["max_uses"] == expected_uses
    expires_at = service.create_invite.await_args.kwargs["expires_at"]
    assert (expires_at is not None) is has_expiry
    deep_link.assert_awaited_once_with(bot, "invite_random-token")
    assert "https://t.me/example_bot" in message[1].await_args.args[0]


@pytest.mark.asyncio
async def test_configured_mini_app_invite_uses_startapp_without_logging_token(
    callback: CallbackQuery,
    message: tuple[Message, AsyncMock],
    session_service: AsyncMock,
) -> None:
    service = AsyncMock()
    service.create_invite.return_value = "one-time_token"
    bot = Bot(token="123456:example")
    callback.as_(bot)
    bot.get_me = AsyncMock(
        return_value=User(id=1, is_bot=True, first_name="Bot", username="ministry_bot")
    )
    try:
        await club_action(
            callback,
            ClubAction(club_id=UUID(int=7), action="invite1"),
            service,
            session_service,
            mini_app_url="https://mini.example.org",
        )
    finally:
        await bot.session.close()

    text = message[1].await_args.args[0]
    assert "https://t.me/ministry_bot?startapp=one-time_token" in text
    assert "?start=" not in text
