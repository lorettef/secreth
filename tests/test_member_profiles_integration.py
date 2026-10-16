"""Registered member profiles against the migrated PostgreSQL schema."""

import asyncio
import os
from unittest.mock import patch
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Club, ClubMembership, Player, TelegramUser
from app.domain.enums import MembershipRole, MembershipStatus
from app.services.membership import (
    MembershipError,
    MembershipErrorCode,
    MembershipService,
)


@pytest.fixture(scope="session")
def test_database_url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a dedicated PostgreSQL test database")
    parsed = make_url(url)
    if parsed.drivername != "postgresql+asyncpg" or not parsed.database.endswith(
        "_test"
    ):
        pytest.fail(
            "TEST_DATABASE_URL must point to a PostgreSQL database ending in _test"
        )
    with patch.dict(os.environ, {"DATABASE_URL": url}):
        command.upgrade(Config("alembic.ini"), "head")
    return url


async def add_membership(session, club_id, user, telegram_id: int) -> TelegramUser:
    if user is None:
        user = TelegramUser(
            telegram_user_id=telegram_id, first_name=f"User {telegram_id}"
        )
        session.add(user)
        await session.flush()
    session.add(
        ClubMembership(
            club_id=club_id,
            telegram_user_id=user.id,
            role=MembershipRole.PLAYER,
            status=MembershipStatus.ACTIVE,
        )
    )
    return user


@pytest_asyncio.fixture
async def profile_data(test_database_url):
    engine = create_async_engine(test_database_url)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        factory = async_sessionmaker(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        async with factory() as session, session.begin():
            first_club = Club(name="First profile club", timezone="UTC")
            second_club = Club(name="Second profile club", timezone="UTC")
            session.add_all((first_club, second_club))
            await session.flush()
            first_user = await add_membership(session, first_club.id, None, 72101)
            await add_membership(session, first_club.id, None, 72102)
            await add_membership(session, second_club.id, first_user, 72101)
            await add_membership(session, second_club.id, None, 72103)
        yield MembershipService(factory), factory, first_club.id, second_club.id
        await transaction.rollback()
    await engine.dispose()


def assert_error(
    code: MembershipErrorCode, error: pytest.ExceptionInfo[MembershipError]
) -> None:
    assert error.value.code is code


@pytest.mark.asyncio
async def test_create_member_profile_normalizes_and_preserves_display_name(
    profile_data,
) -> None:
    service, factory, club_id, _ = profile_data

    profile = await service.create_member_profile(
        72101,
        club_id,
        first_name="  Максим\t",
        last_name="  Иванов  ",
        school_nickname=" Max\u00a0Power ",
    )

    assert (profile.first_name, profile.last_name) == ("Максим", "Иванов")
    assert profile.school_nickname == "Max Power"
    assert profile.display_name == "Максим Иванов"
    async with factory() as session:
        saved = await session.get(Player, profile.id)
        assert saved.club_id == club_id
        assert saved.display_name == "Максим Иванов"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "code"),
    [
        ("first_name", MembershipErrorCode.FIRST_NAME_REQUIRED),
        ("last_name", MembershipErrorCode.LAST_NAME_REQUIRED),
        ("school_nickname", MembershipErrorCode.SCHOOL_NICKNAME_REQUIRED),
    ],
)
async def test_create_member_profile_rejects_blank_required_fields(
    profile_data, field, code
) -> None:
    service, factory, club_id, _ = profile_data
    values = {
        "first_name": "Max",
        "last_name": "Ivanov",
        "school_nickname": "max",
    }
    values[field] = " \t\u00a0 "

    with pytest.raises(MembershipError) as error:
        await service.create_member_profile(72101, club_id, **values)

    assert_error(code, error)
    async with factory() as session:
        assert await session.scalar(select(Player.id)) is None


@pytest.mark.asyncio
async def test_same_school_nickname_is_rejected_within_club(profile_data) -> None:
    service, _, club_id, _ = profile_data
    await service.create_member_profile(
        72101,
        club_id,
        first_name="Max",
        last_name="One",
        school_nickname="same",
    )

    with pytest.raises(MembershipError) as error:
        await service.create_member_profile(
            72102,
            club_id,
            first_name="Max",
            last_name="Two",
            school_nickname="same",
        )

    assert_error(MembershipErrorCode.SCHOOL_NICKNAME_CONFLICT, error)


@pytest.mark.asyncio
async def test_school_nickname_uniqueness_is_case_insensitive(profile_data) -> None:
    service, _, club_id, _ = profile_data
    await service.create_member_profile(
        72101,
        club_id,
        first_name="Max",
        last_name="One",
        school_nickname="SchoolNick",
    )

    with pytest.raises(MembershipError) as error:
        await service.create_member_profile(
            72102,
            club_id,
            first_name="Max",
            last_name="Two",
            school_nickname="schoolnick",
        )

    assert_error(MembershipErrorCode.SCHOOL_NICKNAME_CONFLICT, error)


@pytest.mark.asyncio
async def test_same_school_nickname_is_allowed_in_different_clubs(profile_data) -> None:
    service, _, first_club, second_club = profile_data
    first = await service.create_member_profile(
        72101,
        first_club,
        first_name="Max",
        last_name="One",
        school_nickname="SameNick",
    )
    second = await service.create_member_profile(
        72101,
        second_club,
        first_name="Max",
        last_name="One",
        school_nickname="samenick",
    )

    assert first.id != second.id


@pytest.mark.asyncio
async def test_update_member_profile(profile_data) -> None:
    service, factory, club_id, _ = profile_data
    created = await service.create_member_profile(
        72101,
        club_id,
        first_name="Old",
        last_name="Name",
        school_nickname="oldnick",
    )

    updated = await service.update_member_profile(
        72101,
        club_id,
        first_name=" New ",
        last_name=" Person ",
        school_nickname=" NewNick ",
    )

    assert updated.id == created.id
    assert updated.display_name == "New Person"
    assert updated.school_nickname == "NewNick"
    async with factory() as session:
        saved = await session.get(Player, created.id)
        assert (saved.first_name, saved.last_name, saved.school_nickname) == (
            "New",
            "Person",
            "NewNick",
        )


@pytest.mark.asyncio
async def test_update_cannot_take_another_members_nickname_and_rolls_back(
    profile_data,
) -> None:
    service, factory, club_id, _ = profile_data
    await service.create_member_profile(
        72101,
        club_id,
        first_name="First",
        last_name="Member",
        school_nickname="taken",
    )
    second = await service.create_member_profile(
        72102,
        club_id,
        first_name="Second",
        last_name="Member",
        school_nickname="available",
    )

    with pytest.raises(MembershipError) as error:
        await service.update_member_profile(
            72102,
            club_id,
            first_name="Changed",
            last_name="Name",
            school_nickname="TAKEN",
        )

    assert_error(MembershipErrorCode.SCHOOL_NICKNAME_CONFLICT, error)
    async with factory() as session:
        saved = await session.get(Player, second.id)
        assert (saved.display_name, saved.school_nickname) == (
            "Second Member",
            "available",
        )


@pytest.mark.asyncio
async def test_historical_player_without_profile_fields_remains_readable(
    profile_data,
) -> None:
    _, factory, club_id, _ = profile_data
    async with factory() as session, session.begin():
        historical = Player(club_id=club_id, display_name="Historical Player")
        session.add(historical)
        await session.flush()
        player_id = historical.id

    async with factory() as session:
        saved = await session.get(Player, player_id)
        assert saved.display_name == "Historical Player"
        assert saved.first_name is None
        assert saved.last_name is None
        assert saved.school_nickname is None


@pytest.mark.asyncio
async def test_profile_operations_enforce_club_scope(profile_data) -> None:
    service, _, first_club, second_club = profile_data
    with pytest.raises(MembershipError) as error:
        await service.create_member_profile(
            72103,
            first_club,
            first_name="Other",
            last_name="Club",
            school_nickname="outsider",
        )
    assert_error(MembershipErrorCode.NOT_MEMBER, error)

    await service.create_member_profile(
        72103,
        second_club,
        first_name="Other",
        last_name="Club",
        school_nickname="member",
    )
    with pytest.raises(MembershipError) as error:
        await service.update_member_profile(
            72103,
            first_club,
            first_name="Cross",
            last_name="Club",
            school_nickname="cross",
        )
    assert_error(MembershipErrorCode.NOT_MEMBER, error)


@pytest.mark.asyncio
async def test_concurrent_registration_of_same_nickname_creates_one_profile(
    test_database_url,
) -> None:
    engine = create_async_engine(test_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    telegram_ids = (uuid4().int % 10_000_000_000, uuid4().int % 10_000_000_000)
    club_id = None
    user_ids = []
    try:
        async with factory() as session, session.begin():
            club = Club(name="Concurrent profile club", timezone="UTC")
            session.add(club)
            await session.flush()
            club_id = club.id
            for telegram_id in telegram_ids:
                user = await add_membership(session, club.id, None, telegram_id)
                user_ids.append(user.id)

        service = MembershipService(factory)
        results = await asyncio.wait_for(
            asyncio.gather(
                *(
                    service.create_member_profile(
                        telegram_id,
                        club_id,
                        first_name="Concurrent",
                        last_name=str(index),
                        school_nickname="OneNick",
                    )
                    for index, telegram_id in enumerate(telegram_ids)
                ),
                return_exceptions=True,
            ),
            timeout=10,
        )

        assert sum(not isinstance(result, Exception) for result in results) == 1
        errors = [result for result in results if isinstance(result, Exception)]
        assert len(errors) == 1
        assert isinstance(errors[0], MembershipError)
        assert errors[0].code is MembershipErrorCode.SCHOOL_NICKNAME_CONFLICT
        async with factory() as session:
            assert (
                len(
                    (
                        await session.scalars(
                            select(Player).where(Player.club_id == club_id)
                        )
                    ).all()
                )
                == 1
            )
    finally:
        if club_id is not None:
            async with factory() as session, session.begin():
                await session.execute(delete(Player).where(Player.club_id == club_id))
                await session.execute(
                    delete(ClubMembership).where(ClubMembership.club_id == club_id)
                )
                await session.execute(
                    delete(TelegramUser).where(TelegramUser.id.in_(user_ids))
                )
                await session.execute(delete(Club).where(Club.id == club_id))
        await engine.dispose()
