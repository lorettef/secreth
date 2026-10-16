"""Telegram Mini App initData verification without trusting client identity."""

import hashlib
import hmac
import json
from urllib.parse import urlencode

import pytest

from app.auth.telegram import (
    TelegramAuthError,
    TelegramAuthErrorCode,
    verify_init_data,
)

BOT_TOKEN = "123456:stage-1f-secret"
NOW = 2_000_000_000


def signed_init_data(
    *,
    telegram_id: int = 12345,
    auth_date: int = NOW,
    extra: dict[str, str] | None = None,
) -> str:
    values = {
        "auth_date": str(auth_date),
        "query_id": "AAExampleQuery",
        "user": json.dumps(
            {"id": telegram_id, "first_name": "Иван"},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        **(extra or {}),
    }
    data_check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(values.items()) if key != "signature"
    )
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(
        secret, data_check_string.encode(), hashlib.sha256
    ).hexdigest()
    return urlencode(values)


def assert_error(code: TelegramAuthErrorCode, raw: str) -> None:
    with pytest.raises(TelegramAuthError) as error:
        verify_init_data(raw, BOT_TOKEN, now=NOW)
    assert error.value.code is code


def test_valid_signature_and_optional_signature_compatibility() -> None:
    identity = verify_init_data(
        signed_init_data(extra={"signature": "optional-ed25519-value"}),
        BOT_TOKEN,
        now=NOW,
    )
    assert identity.telegram_id == 12345
    assert identity.auth_date == NOW


def test_changed_telegram_id_with_old_signature_is_rejected() -> None:
    raw = signed_init_data().replace("12345", "99999", 1)
    assert_error(TelegramAuthErrorCode.INVALID, raw)


def test_changed_signed_content_is_rejected() -> None:
    assert_error(
        TelegramAuthErrorCode.INVALID,
        signed_init_data().replace("AAExampleQuery", "ChangedQuery"),
    )


def test_invalid_or_missing_hash_is_rejected() -> None:
    raw = signed_init_data()
    assert_error(TelegramAuthErrorCode.INVALID, raw.replace("hash=", "hash=00"))
    assert_error(TelegramAuthErrorCode.INVALID, raw.rsplit("&hash=", 1)[0])


def test_expired_and_future_auth_date_are_rejected() -> None:
    assert_error(
        TelegramAuthErrorCode.EXPIRED,
        signed_init_data(auth_date=NOW - 3601),
    )
    assert_error(
        TelegramAuthErrorCode.FUTURE,
        signed_init_data(auth_date=NOW + 31),
    )


@pytest.mark.parametrize(
    "raw",
    [
        signed_init_data() + "&auth_date=2000000000",
        signed_init_data() + "&user=%7B%7D",
        signed_init_data() + "&=value",
        signed_init_data() + "%ZZ",
        "not-a-query-field",
    ],
)
def test_duplicate_or_malformed_fields_are_rejected(raw: str) -> None:
    assert_error(TelegramAuthErrorCode.INVALID, raw)


def test_missing_user_is_rejected_after_valid_signature() -> None:
    raw = signed_init_data()
    pairs = [part for part in raw.split("&") if not part.startswith("user=")]
    unsigned = "&".join(part for part in pairs if not part.startswith("hash="))
    data_check_string = "\n".join(
        f"{key}={value}"
        for key, value in sorted(
            {"auth_date": str(NOW), "query_id": "AAExampleQuery"}.items()
        )
    )
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
    assert_error(TelegramAuthErrorCode.USER_REQUIRED, f"{unsigned}&hash={digest}")
