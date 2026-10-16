"""FastAPI dependencies for trusted Telegram identities and shared services."""

from typing import Annotated

from fastapi import Header, HTTPException, Request, status

from app.auth.telegram import TelegramAuthError, TelegramIdentity, verify_init_data
from app.config import Settings
from app.services.http_identity import HttpIdentityService


def authenticated_telegram(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> TelegramIdentity:
    if authorization is None:
        raise _unauthorized("TELEGRAM_AUTH_REQUIRED")
    scheme, separator, raw_init_data = authorization.partition(" ")
    if not separator or scheme.lower() != "tma" or not raw_init_data:
        raise _unauthorized("TELEGRAM_AUTH_REQUIRED")
    settings: Settings = request.app.state.settings
    try:
        return verify_init_data(
            raw_init_data,
            settings.bot_token,
            max_age_seconds=settings.telegram_init_data_max_age_seconds,
            future_skew_seconds=settings.telegram_init_data_future_skew_seconds,
        )
    except TelegramAuthError as error:
        raise _unauthorized(error.code.value) from None


def identity_service(request: Request) -> HttpIdentityService:
    return HttpIdentityService(request.app.state.session_factory)


def _unauthorized(code: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=code,
        headers={"WWW-Authenticate": "tma"},
    )
