"""One-time command for the first club administrator on an empty database."""

import argparse
import asyncio
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select, text

from app.config import Settings
from app.db.models import Club, ClubMembership, Player, TelegramUser
from app.db.session import create_engine, create_session_factory
from app.domain.enums import MembershipRole, MembershipStatus


async def bootstrap_first_admin(
    database_url: str, *, telegram_id: int, club_name: str, timezone_name: str, display_name: str
) -> None:
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Unknown club timezone") from exc
    if not club_name.strip() or not display_name.strip():
        raise ValueError("Club and player names must be non-empty")

    engine = create_engine(database_url)
    try:
        factory = create_session_factory(engine)
        async with factory() as session, session.begin():
            await session.execute(text("SELECT pg_advisory_xact_lock(73481021)"))
            if await session.scalar(select(Club.id).limit(1)) is not None:
                raise ValueError("Bootstrap requires an empty club database")
            user = await session.scalar(
                select(TelegramUser).where(TelegramUser.telegram_user_id == telegram_id)
            )
            if user is None:
                user = TelegramUser(telegram_user_id=telegram_id, first_name=display_name.strip()[:255])
                session.add(user)
                await session.flush()
            club = Club(name=club_name.strip()[:255], timezone=timezone_name)
            session.add(club)
            await session.flush()
            session.add(Player(club_id=club.id, display_name=display_name.strip()[:255], telegram_user_id=user.id))
            session.add(
                ClubMembership(
                    club_id=club.id,
                    telegram_user_id=user.id,
                    role=MembershipRole.ADMIN,
                    status=MembershipStatus.ACTIVE,
                )
            )
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the first club and administrator")
    parser.add_argument("--telegram-id", type=int, required=True)
    parser.add_argument("--club", required=True)
    parser.add_argument("--timezone", default="UTC")
    parser.add_argument("--player", required=True, help="Administrator's player display name")
    arguments = parser.parse_args()
    settings = Settings()
    try:
        asyncio.run(
            bootstrap_first_admin(
                settings.database_url,
                telegram_id=arguments.telegram_id,
                club_name=arguments.club,
                timezone_name=arguments.timezone,
                display_name=arguments.player,
            )
        )
    except ValueError as exc:
        parser.exit(2, f"{exc}\n")
    print("Initial club administrator created")


if __name__ == "__main__":
    main()
