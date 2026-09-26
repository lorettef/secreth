"""Long-polling application entry point."""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from sqlalchemy import text

from app.bot.handlers.start import router as start_router
from app.config import Settings
from app.db.session import create_engine, create_session_factory

logger = logging.getLogger(__name__)


def create_dispatcher() -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.include_router(start_router)
    return dispatcher


async def main() -> None:
    settings = Settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))
        logger.info("Database connection ready")

        async with Bot(token=settings.bot_token) as bot:
            logger.info("Starting Telegram long polling")
            await create_dispatcher().start_polling(bot)
    finally:
        await engine.dispose()
        logger.info("Application stopped")


if __name__ == "__main__":
    asyncio.run(main())
