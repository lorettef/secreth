#!/usr/bin/env python3
"""Operator-only normalized import; explicit club or exactly one club required."""

import argparse
import asyncio
import os
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from sqlalchemy import select
from app.db.models import Club
from app.db.session import create_engine, create_session_factory
from app.services.spreadsheets import ImportService, read_import


async def run(arguments):
    load_dotenv()
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise ValueError("DATABASE_URL is required")
    engine = create_engine(url)
    try:
        factory = create_session_factory(engine)
        async with factory() as db:
            clubs = (await db.scalars(select(Club.id))).all()
        club = arguments.club_id or (clubs[0] if len(clubs) == 1 else None)
        if club is None:
            raise ValueError("Use --club-id UUID when there is not exactly one club")
        report = await ImportService(factory).run(
            club, read_import(Path(arguments.file)), apply=arguments.apply
        )
        for name, count in report.counts.items():
            print(f"{name}: {count}")
        for row in report.tables["Players"]:
            print(f"Player: {row.get('display_name')} ({row.get('id', 'invalid')})")
        for level, items in (("Warning", report.warnings), ("Error", report.errors)):
            for item in items:
                print(f"{level}: {item}")
        print(f"Warnings: {len(report.warnings)}; Errors: {len(report.errors)}")
        print(
            "APPLIED"
            if arguments.apply and not report.errors
            else "APPLY NOT PERFORMED"
        )
        return 2 if report.errors else 0
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file")
    parser.add_argument("--club-id", type=UUID)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        code = asyncio.run(run(args))
    except Exception as error:
        # Do not print URLs, SQL parameters or credentials from infrastructure exceptions.
        parser.exit(
            2,
            f"Import failed ({type(error).__name__}); verify input/configuration/database.\n",
        )
    raise SystemExit(code)


if __name__ == "__main__":
    main()
