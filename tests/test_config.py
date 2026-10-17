"""Smoke tests for environment configuration."""

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_settings_load_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "123456:example")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:password@localhost:5432/stats")
    monkeypatch.setenv("LOG_LEVEL", "debug")

    settings = Settings(_env_file=None)

    assert settings.bot_token == "123456:example"
    assert settings.database_url.endswith("/stats")
    assert settings.log_level == "DEBUG"


def test_settings_require_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_http_security_settings_are_bounded() -> None:
    settings = Settings(
        _env_file=None,
        bot_token="123456:example",
        database_url="postgresql+asyncpg://user:password@localhost:5432/stats",
        mini_app_allowed_origins=["https://mini.example.org/"],
        mini_app_url="https://mini.example.org/app/",
    )
    assert settings.telegram_init_data_max_age_seconds == 3600
    assert settings.mini_app_allowed_origins == ["https://mini.example.org"]
    assert settings.mini_app_url == "https://mini.example.org/app"
    for values in (
        {"telegram_init_data_max_age_seconds": 86401},
        {"mini_app_allowed_origins": ["*"]},
        {"mini_app_allowed_origins": ["http://mini.example.org"]},
        {"mini_app_url": "http://mini.example.org"},
        {"mini_app_url": "https://mini.example.org/?token=secret"},
    ):
        with pytest.raises(ValidationError):
            Settings(
                _env_file=None,
                bot_token="123456:example",
                database_url="postgresql+asyncpg://user:password@localhost:5432/stats",
                **values,
            )
