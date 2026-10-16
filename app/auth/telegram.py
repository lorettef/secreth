"""Strict verification of Telegram Mini App initData."""

import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import parse_qsl


class TelegramAuthErrorCode(StrEnum):
    INVALID = "TELEGRAM_AUTH_INVALID"
    EXPIRED = "TELEGRAM_AUTH_EXPIRED"
    FUTURE = "TELEGRAM_AUTH_FUTURE"
    USER_REQUIRED = "TELEGRAM_AUTH_USER_REQUIRED"


class TelegramAuthError(ValueError):
    def __init__(self, code: TelegramAuthErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True)
class TelegramIdentity:
    telegram_id: int
    auth_date: int


_HEX_HASH = re.compile(r"[0-9a-fA-F]{64}\Z")
_VALID_PERCENT_ESCAPE = re.compile(r"%(?:[0-9a-fA-F]{2})")


def verify_init_data(
    raw_init_data: str,
    bot_token: str,
    *,
    max_age_seconds: int = 3600,
    future_skew_seconds: int = 30,
    now: int | None = None,
) -> TelegramIdentity:
    """Verify Telegram's HMAC and return identity only from signed user data."""
    if not raw_init_data or not bot_token or len(raw_init_data.encode("utf-8")) > 8192:
        raise TelegramAuthError(TelegramAuthErrorCode.INVALID)
    _reject_invalid_percent_encoding(raw_init_data)
    try:
        pairs = parse_qsl(
            raw_init_data,
            keep_blank_values=True,
            strict_parsing=True,
            encoding="utf-8",
            errors="strict",
        )
    except (UnicodeError, ValueError) as error:
        raise TelegramAuthError(TelegramAuthErrorCode.INVALID) from error
    if not pairs or any(not key or "\n" in key or "\r" in key for key, _ in pairs):
        raise TelegramAuthError(TelegramAuthErrorCode.INVALID)
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise TelegramAuthError(TelegramAuthErrorCode.INVALID)
    values = dict(pairs)
    received_hash = values.get("hash")
    if received_hash is None or not _HEX_HASH.fullmatch(received_hash):
        raise TelegramAuthError(TelegramAuthErrorCode.INVALID)

    # Telegram's bot-token validation excludes both integrity fields. The
    # optional `signature` belongs to the separate Ed25519 validation method.
    data_check_string = "\n".join(
        f"{key}={value}"
        for key, value in sorted(pairs)
        if key not in {"hash", "signature"}
    )
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected_hash = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(received_hash.lower(), expected_hash):
        raise TelegramAuthError(TelegramAuthErrorCode.INVALID)

    try:
        auth_date = int(values["auth_date"])
    except (KeyError, TypeError, ValueError) as error:
        raise TelegramAuthError(TelegramAuthErrorCode.INVALID) from error
    current_time = int(time.time()) if now is None else now
    if auth_date > current_time + future_skew_seconds:
        raise TelegramAuthError(TelegramAuthErrorCode.FUTURE)
    if current_time - auth_date > max_age_seconds:
        raise TelegramAuthError(TelegramAuthErrorCode.EXPIRED)

    try:
        user = json.loads(values["user"])
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise TelegramAuthError(TelegramAuthErrorCode.USER_REQUIRED) from error
    telegram_id = user.get("id") if isinstance(user, dict) else None
    if type(telegram_id) is not int or telegram_id <= 0:
        raise TelegramAuthError(TelegramAuthErrorCode.USER_REQUIRED)
    return TelegramIdentity(telegram_id=telegram_id, auth_date=auth_date)


def _reject_invalid_percent_encoding(value: str) -> None:
    cursor = 0
    while True:
        cursor = value.find("%", cursor)
        if cursor < 0:
            return
        if _VALID_PERCENT_ESCAPE.match(value, cursor) is None:
            raise TelegramAuthError(TelegramAuthErrorCode.INVALID)
        cursor += 3
