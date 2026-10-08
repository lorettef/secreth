"""Startup readiness and redacted diagnostics for one long-polling process."""

import logging
import re
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from app.config import Settings


class RedactingFormatter(logging.Formatter):
    def __init__(self, secrets: tuple[str, ...] = ()) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s: %(message)s")
        self.secrets = tuple(str(s) for s in secrets if s)

    def format(self, record: logging.LogRecord) -> str:
        result = super().format(record)
        for secret in self.secrets:
            result = result.replace(secret, "[REDACTED]")
        result = re.sub(r"\b\d{5,}:[A-Za-z0-9_-]{20,}\b", "[BOT_TOKEN]", result)
        result = re.sub(
            r"(postgres(?:ql)?(?:\+asyncpg)?://)[^\s]+", r"\1[REDACTED]", result
        )
        return result


def configure_logging(settings: Settings) -> None:
    from sqlalchemy.engine import make_url

    handler = logging.StreamHandler()
    handler.setFormatter(
        RedactingFormatter(
            (
                settings.bot_token,
                settings.database_url,
                make_url(settings.database_url).password,
            )
        )
    )
    logging.basicConfig(level=settings.log_level, handlers=[handler], force=True)


async def check_database(engine: AsyncEngine) -> None:
    root = Path(__file__).resolve().parent.parent
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    heads = set(ScriptDirectory.from_config(config).get_heads())
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
        exists = await connection.scalar(
            text("SELECT to_regclass('public.alembic_version')")
        )
        if not exists:
            raise RuntimeError("Database schema is missing. Run alembic upgrade head.")
        revisions = set(
            (
                await connection.execute(
                    text("SELECT version_num FROM alembic_version")
                )
            ).scalars()
        )
        if revisions != heads:
            raise RuntimeError("Database schema is outdated. Run alembic upgrade head.")
    logging.getLogger(__name__).info(
        "Database schema ready: revisions=%s", sorted(heads)
    )
