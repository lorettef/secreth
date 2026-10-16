"""CLI for operator-only initial club and OWNER setup."""

import argparse
import asyncio
import uuid

from app.config import Settings
from app.db.session import create_engine, create_session_factory
from app.services.bootstrap import BootstrapError, ClubBootstrapService, OwnerAssignment


async def create_club(database_url: str, *, name: str, timezone_name: str) -> uuid.UUID:
    engine = create_engine(database_url)
    try:
        service = ClubBootstrapService(create_session_factory(engine))
        return await service.create_club(name, timezone_name)
    finally:
        await engine.dispose()


async def create_initial_invite(database_url: str, *, club_id: uuid.UUID) -> str:
    engine = create_engine(database_url)
    try:
        service = ClubBootstrapService(create_session_factory(engine))
        return await service.create_initial_invite(club_id)
    finally:
        await engine.dispose()


async def assign_owner(
    database_url: str, *, club_id: uuid.UUID, telegram_id: int
) -> OwnerAssignment:
    engine = create_engine(database_url)
    try:
        service = ClubBootstrapService(create_session_factory(engine))
        return await service.assign_owner(club_id, telegram_id)
    finally:
        await engine.dispose()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bootstrap a club, its first invitation, and its OWNER"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create-club", help="Create a club without members")
    create.add_argument("--club", required=True)
    create.add_argument("--timezone")

    invite = commands.add_parser(
        "create-invite", help="Create the club's single bootstrap invitation"
    )
    invite.add_argument("--club-id", type=uuid.UUID, required=True)

    owner = commands.add_parser(
        "assign-owner", help="Assign a registered club member as OWNER"
    )
    owner.add_argument("--club-id", type=uuid.UUID, required=True)
    owner.add_argument("--telegram-id", type=int, required=True)
    return parser


def main() -> None:
    parser = _parser()
    arguments = parser.parse_args()
    settings = Settings()
    try:
        if arguments.command == "create-club":
            club_id = asyncio.run(
                create_club(
                    settings.database_url,
                    name=arguments.club,
                    timezone_name=arguments.timezone or settings.default_timezone,
                )
            )
            print(f"Club created: {club_id}")
        elif arguments.command == "create-invite":
            token = asyncio.run(
                create_initial_invite(
                    settings.database_url,
                    club_id=arguments.club_id,
                )
            )
            print(f"Bootstrap invitation token: {token}")
        else:
            result = asyncio.run(
                assign_owner(
                    settings.database_url,
                    club_id=arguments.club_id,
                    telegram_id=arguments.telegram_id,
                )
            )
            state = "assigned" if result.changed else "already assigned"
            print(f"OWNER {state}: membership {result.membership_id}")
    except BootstrapError as exc:
        parser.exit(2, f"{exc.code.value}\n")


if __name__ == "__main__":
    main()
