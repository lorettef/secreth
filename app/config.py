"""Environment-backed application settings."""

import logging
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    bot_token: str = Field(min_length=1, repr=False)
    database_url: str = Field(repr=False)
    log_level: str = "INFO"
    default_timezone: str = "UTC"
    telegram_init_data_max_age_seconds: int = Field(default=3600, ge=1, le=86400)
    telegram_init_data_future_skew_seconds: int = Field(default=30, ge=0, le=300)
    mini_app_allowed_origins: list[str] = Field(default_factory=list)
    mini_app_url: str | None = None

    @field_validator("default_timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("DEFAULT_TIMEZONE must be an IANA timezone") from exc
        return value

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        try:
            driver = make_url(value).drivername
        except Exception as exc:
            raise ValueError("DATABASE_URL must be a valid SQLAlchemy URL") from exc
        if driver != "postgresql+asyncpg":
            raise ValueError("DATABASE_URL must use postgresql+asyncpg")
        return value

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in logging.getLevelNamesMapping():
            raise ValueError("LOG_LEVEL must be a standard logging level")
        return normalized

    @field_validator("mini_app_allowed_origins")
    @classmethod
    def validate_mini_app_allowed_origins(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            origin = value.rstrip("/")
            parsed = urlsplit(origin)
            if (
                value == "*"
                or parsed.scheme != "https"
                or not parsed.netloc
                or parsed.path
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(
                    "MINI_APP_ALLOWED_ORIGINS must contain exact HTTPS origins"
                )
            normalized.append(origin)
        if len(set(normalized)) != len(normalized):
            raise ValueError("MINI_APP_ALLOWED_ORIGINS must not contain duplicates")
        return normalized

    @field_validator("mini_app_url")
    @classmethod
    def validate_mini_app_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        normalized = value.rstrip("/")
        parsed = urlsplit(normalized)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("MINI_APP_URL must be an absolute HTTPS URL")
        return normalized
