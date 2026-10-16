"""Initial club invitation and OWNER setup against PostgreSQL."""

import asyncio
import os
import uuid
from unittest.mock import patch

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import (
    AuditLog,
    Club,
    ClubMembership,
    InviteToken,
    Player,
    TelegramUser,
)
from app.domain.enums import MembershipRole, MembershipStatus
from app.services.bootstrap import (
    BootstrapError,
    BootstrapErrorCode,
    ClubBootstrapService,
    OwnerAssignment,
)
from app.services.membership import (
    MembershipError,
    MembershipErrorCode,
    MembershipService,
    TelegramProfile,
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


@pytest_asyncio.fixture
async def bootstrap(test_database_url: str):
    engine = create_async_engine(test_database_url)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        factory = async_sessionmaker(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        yield ClubBootstrapService(factory), MembershipService(factory), factory
        await transaction.rollback()
    await engine.dispose()


async def add_member(
    factory: async_sessionmaker[AsyncSession],
    club_id: uuid.UUID,
    telegram_id: int,
    *,
    role: MembershipRole = MembershipRole.PLAYER,
    status: MembershipStatus = MembershipStatus.ACTIVE,
    with_player: bool = False,
) -> ClubMembership:
    async with factory() as session, session.begin():
        user = TelegramUser(telegram_user_id=telegram_id, first_name=str(telegram_id))
        session.add(user)
        await session.flush()
        membership = ClubMembership(
            club_id=club_id,
            telegram_user_id=user.id,
            role=role,
            status=status,
        )
        session.add(membership)
        if with_player:
            session.add(
                Player(
                    club_id=club_id,
                    telegram_user_id=user.id,
                    display_name=str(telegram_id),
                )
            )
        await session.flush()
        return membership


def assert_bootstrap_error(
    expected: BootstrapErrorCode,
    error: pytest.ExceptionInfo[BootstrapError],
) -> None:
    assert error.value.code is expected


@pytest.mark.asyncio
async def test_system_bootstrap_invite_is_single_use_without_expiry_and_audited(
    bootstrap,
) -> None:
    service, _, factory = bootstrap
    club_id = await service.create_club("New club", "Asia/Yakutsk")
    token = await service.create_initial_invite(club_id)

    async with factory() as session:
        invitation = await session.scalar(
            select(InviteToken).where(InviteToken.token == token)
        )
        audits = (
            await session.scalars(select(AuditLog).where(AuditLog.club_id == club_id))
        ).all()

    assert invitation.created_by is None
    assert invitation.is_bootstrap
    assert invitation.max_uses == 1
    assert invitation.expires_at is None
    assert invitation.uses_count == 0
    audits_by_action = {audit.action: audit for audit in audits}
    assert set(audits_by_action) == {
        "BOOTSTRAP_CLUB_CREATE",
        "BOOTSTRAP_INVITE_CREATE",
    }
    assert all(audit.actor_player_id is None for audit in audits)
    invite_audit = audits_by_action["BOOTSTRAP_INVITE_CREATE"]
    assert invite_audit.after_data == {
        "origin": "SYSTEM_CLI",
        "max_uses": 1,
        "expires_at": None,
    }
    assert token not in str(invite_audit.after_data)

    with pytest.raises(BootstrapError) as error:
        await service.create_initial_invite(club_id)
    assert_bootstrap_error(BootstrapErrorCode.BOOTSTRAP_INVITE_EXISTS, error)
    async with factory() as session:
        assert (
            await session.scalar(
                select(func.count(InviteToken.id)).where(
                    InviteToken.club_id == club_id,
                    InviteToken.is_bootstrap.is_(True),
                )
            )
            == 1
        )
    with pytest.raises(IntegrityError):
        async with factory() as session, session.begin():
            session.add(
                InviteToken(
                    club_id=club_id,
                    token="direct-bootstrap-duplicate",
                    created_by=None,
                    is_bootstrap=True,
                    max_uses=1,
                )
            )
            await session.flush()


@pytest.mark.asyncio
async def test_bootstrap_invite_can_be_consumed_only_once(bootstrap) -> None:
    service, memberships, factory = bootstrap
    club_id = await service.create_club("Invite club", "UTC")
    token = await service.create_initial_invite(club_id)
    invitation = await memberships.preview_invite(token)
    await memberships.register_telegram_user(
        TelegramProfile(93101, None, "First", None)
    )
    await memberships.register_telegram_user(
        TelegramProfile(93102, None, "Second", None)
    )

    first = await memberships.join_invite(93101, invitation.id)
    replay = await memberships.join_invite(93101, invitation.id)
    assert first.id == replay.id == club_id
    with pytest.raises(MembershipError) as error:
        await memberships.join_invite(93102, invitation.id)
    assert error.value.code is MembershipErrorCode.INVITE_EXHAUSTED

    async with factory() as session:
        persisted = await session.get(InviteToken, invitation.id)
        assert persisted.uses_count == 1
        assert (
            await session.scalar(
                select(func.count(ClubMembership.id)).where(
                    ClubMembership.club_id == club_id
                )
            )
            == 1
        )


@pytest.mark.asyncio
async def test_registered_member_is_assigned_owner_idempotently_and_audited(
    bootstrap,
) -> None:
    service, _, factory = bootstrap
    club_id = await service.create_club("Owner club", "UTC")
    membership = await add_member(factory, club_id, 93103)

    first = await service.assign_owner(club_id, 93103)
    second = await service.assign_owner(club_id, 93103)

    assert first == OwnerAssignment(membership.id, changed=True)
    assert second == OwnerAssignment(membership.id, changed=False)
    async with factory() as session:
        persisted = await session.get(ClubMembership, membership.id)
        audits = (
            await session.scalars(
                select(AuditLog).where(
                    AuditLog.club_id == club_id,
                    AuditLog.action == "BOOTSTRAP_OWNER_ASSIGN",
                )
            )
        ).all()
    assert persisted.role is MembershipRole.OWNER
    assert len(audits) == 1
    assert audits[0].actor_player_id is None
    assert audits[0].before_data == {"role": "PLAYER"}
    assert audits[0].after_data == {"origin": "SYSTEM_CLI", "role": "OWNER"}


@pytest.mark.asyncio
async def test_unregistered_or_foreign_member_cannot_be_assigned_owner(
    bootstrap,
) -> None:
    service, _, factory = bootstrap
    target_club = await service.create_club("Target", "UTC")
    foreign_club = await service.create_club("Foreign", "UTC")
    await add_member(factory, foreign_club, 93104)

    for telegram_id in (93104, 93999):
        with pytest.raises(BootstrapError) as error:
            await service.assign_owner(target_club, telegram_id)
        assert_bootstrap_error(BootstrapErrorCode.TARGET_NOT_MEMBER, error)


@pytest.mark.asyncio
async def test_second_owner_is_rejected_and_owner_blocks_new_bootstrap_invite(
    bootstrap,
) -> None:
    service, _, factory = bootstrap
    club_id = await service.create_club("Single owner", "UTC")
    first = await add_member(factory, club_id, 93105)
    second = await add_member(factory, club_id, 93106)
    await service.assign_owner(club_id, 93105)

    with pytest.raises(BootstrapError) as error:
        await service.assign_owner(club_id, 93106)
    assert_bootstrap_error(BootstrapErrorCode.OWNER_ALREADY_ASSIGNED, error)
    with pytest.raises(BootstrapError) as error:
        await service.create_initial_invite(club_id)
    assert_bootstrap_error(BootstrapErrorCode.OWNER_ALREADY_ASSIGNED, error)

    async with factory() as session:
        assert (
            await session.get(ClubMembership, first.id)
        ).role is MembershipRole.OWNER
        assert (
            await session.get(ClubMembership, second.id)
        ).role is MembershipRole.PLAYER


@pytest.mark.asyncio
async def test_existing_player_created_invitation_remains_compatible(bootstrap) -> None:
    service, memberships, factory = bootstrap
    club_id = await service.create_club("Legacy invite", "UTC")
    admin = await add_member(
        factory,
        club_id,
        93107,
        role=MembershipRole.ADMIN,
        with_player=True,
    )

    token = await memberships.create_invite(93107, club_id, max_uses=2)
    async with factory() as session:
        invitation = await session.scalar(
            select(InviteToken).where(InviteToken.token == token)
        )
        player_id = await session.scalar(
            select(Player.id).where(Player.telegram_user_id == admin.telegram_user_id)
        )
    assert invitation.created_by == player_id
    assert not invitation.is_bootstrap
    assert invitation.max_uses == 2


@pytest.mark.asyncio
async def test_concurrent_owner_assignment_creates_one_owner(
    test_database_url: str,
) -> None:
    engine = create_async_engine(test_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = ClubBootstrapService(factory)
    club_id = await service.create_club("Concurrent owner", "UTC")
    first = await add_member(factory, club_id, 93108)
    second = await add_member(factory, club_id, 93109)

    try:
        results = await asyncio.wait_for(
            asyncio.gather(
                service.assign_owner(club_id, 93108),
                service.assign_owner(club_id, 93109),
                return_exceptions=True,
            ),
            timeout=10,
        )
        successes = [
            result for result in results if isinstance(result, OwnerAssignment)
        ]
        failures = [result for result in results if isinstance(result, BootstrapError)]
        assert len(successes) == 1
        assert successes[0].changed
        assert len(failures) == 1
        assert failures[0].code is BootstrapErrorCode.OWNER_ALREADY_ASSIGNED

        async with factory() as session:
            assert (
                await session.scalar(
                    select(func.count(ClubMembership.id)).where(
                        ClubMembership.club_id == club_id,
                        ClubMembership.role == MembershipRole.OWNER,
                    )
                )
                == 1
            )
    finally:
        async with factory() as session, session.begin():
            await session.execute(delete(AuditLog).where(AuditLog.club_id == club_id))
            await session.execute(
                delete(ClubMembership).where(ClubMembership.club_id == club_id)
            )
            await session.execute(
                delete(TelegramUser).where(
                    TelegramUser.id.in_(
                        (first.telegram_user_id, second.telegram_user_id)
                    )
                )
            )
            await session.execute(delete(Club).where(Club.id == club_id))
        await engine.dispose()
