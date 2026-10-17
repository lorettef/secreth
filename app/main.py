"""Long-polling application entry point."""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.storage.memory import SimpleEventIsolation
from aiogram.types import MenuButtonWebApp, WebAppInfo

from app.bot.handlers.start import router as start_router
from app.bot.handlers.membership import router as membership_router
from app.bot.handlers.admin import router as admin_router
from app.bot.handlers.sessions import router as sessions_router
from app.bot.handlers.games import router as games_router
from app.bot.handlers.events import router as events_router
from app.bot.handlers.stats import router as stats_router
from app.bot.handlers.history import router as history_router
from app.bot.handlers.exports import router as exports_router
from app.bot.handlers.errors import unexpected_error
from app.services.spreadsheets import ExportService
from app.operations import configure_logging, check_database
from app.config import Settings
from app.db.session import create_engine, create_session_factory
from app.services.membership import MembershipService
from app.services.sessions import SessionService
from app.services.games import GameService
from app.services.events import EventService
from app.services.stats import StatsService
from app.services.history import HistoryService

logger = logging.getLogger(__name__)


async def configure_mini_app_menu(bot: Bot, mini_app_url: str | None) -> None:
    """Configure the global WebApp menu only when an HTTPS URL is provided."""
    if mini_app_url is None:
        return
    try:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="Открыть приложение",
                web_app=WebAppInfo(url=mini_app_url),
            )
        )
    except TelegramAPIError:
        logger.warning("Could not configure Telegram Mini App menu button")


def create_dispatcher() -> Dispatcher:
    dispatcher = Dispatcher(events_isolation=SimpleEventIsolation())
    dispatcher.errors.register(unexpected_error)
    dispatcher.include_router(exports_router)
    dispatcher.include_router(start_router)
    dispatcher.include_router(stats_router)
    dispatcher.include_router(history_router)
    dispatcher.include_router(membership_router)
    dispatcher.include_router(admin_router)
    dispatcher.include_router(sessions_router)
    dispatcher.include_router(games_router)
    dispatcher.include_router(events_router)
    return dispatcher


async def main() -> None:
    settings = Settings()
    configure_logging(settings)
    logger.info("Application starting")

    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    membership_service = MembershipService(session_factory)
    session_service = SessionService(session_factory)
    game_service = GameService(session_factory)
    event_service = EventService(session_factory)
    stats_service = StatsService(session_factory)
    history_service = HistoryService(session_factory)
    try:
        await check_database(engine)
        logger.info("Database connection ready")

        async with Bot(token=settings.bot_token) as bot:
            await configure_mini_app_menu(bot, settings.mini_app_url)
            logger.info("Starting Telegram long polling")
            await create_dispatcher().start_polling(
                bot, membership_service=membership_service, session_service=session_service,
                game_service=game_service,
                event_service=event_service,
                stats_service=stats_service,
                history_service=history_service,
                export_service=ExportService(engine),
                mini_app_url=settings.mini_app_url,
            )
    except Exception:
        logger.exception("Application startup/polling failed")
        raise
    finally:
        await engine.dispose()
        logger.info("Application stopped")


if __name__ == "__main__":
    from pydantic import ValidationError
    import sys
    try:
        asyncio.run(main())
    except ValidationError as error:
        # Settings hides input values; startup errors must not dump raw config.
        sys.stderr.write(f"Invalid configuration: {error}\n")
        raise SystemExit(1) from None
    except Exception:
        # Details were logged with redaction inside main().
        raise SystemExit(1) from None
