"""Onboarding behavior against a transactional PostgreSQL test database."""

import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.exc import IntegrityError

from app.db.models import (
    AuditLog,
    Club,
    ClubMembership,
    InviteToken,
    Player,
    PlayerLinkRequest,
    TelegramUser,
)
from app.domain.enums import MembershipRole, MembershipStatus, PlayerLinkStatus
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
    if parsed.drivername != "postgresql+asyncpg" or not parsed.database.endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must point to a PostgreSQL database ending in _test")
    with patch.dict(os.environ, {"DATABASE_URL": url}):
        command.upgrade(Config("alembic.ini"), "head")
    return url


@pytest_asyncio.fixture
async def onboarding(test_database_url: str):
    engine = create_async_engine(test_database_url)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        factory = async_sessionmaker(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        yield MembershipService(factory), factory
        await transaction.rollback()
    await engine.dispose()


async def seed_admin(factory: async_sessionmaker[AsyncSession]) -> tuple[uuid.UUID, uuid.UUID]:
    async with factory() as session, session.begin():
        club = Club(name="Saturday Club", timezone="Asia/Yakutsk")
        user = TelegramUser(telegram_user_id=100, first_name="Admin")
        session.add_all([club, user])
        await session.flush()
        player = Player(club_id=club.id, display_name="Admin", telegram_user_id=user.id)
        membership = ClubMembership(
            club_id=club.id,
            telegram_user_id=user.id,
            role=MembershipRole.ADMIN,
            status=MembershipStatus.ACTIVE,
        )
        session.add_all([player, membership])
        await session.flush()
        return club.id, user.id


async def seed_invite(
    factory: async_sessionmaker[AsyncSession], club_id: uuid.UUID, *,
    is_active: bool = True, expires_at: datetime | None = None,
    max_uses: int | None = None, uses_count: int = 0,
) -> InviteToken:
    async with factory() as session, session.begin():
        player = await session.scalar(select(Player).where(Player.club_id == club_id))
        invite = InviteToken(
            club_id=club_id,
            token=f"test-{uuid.uuid4().hex}",
            created_by=player.id,
            is_active=is_active,
            expires_at=expires_at,
            max_uses=max_uses,
            uses_count=uses_count,
        )
        session.add(invite)
        await session.flush()
        return invite


async def register(service: MembershipService, telegram_id: int, name: str = "Max") -> uuid.UUID:
    return await service.register_telegram_user(
        TelegramProfile(telegram_id, None, name, None)
    )


def assert_error(code: MembershipErrorCode, error: pytest.ExceptionInfo[MembershipError]) -> None:
    assert error.value.code is code


@pytest.mark.asyncio
async def test_new_telegram_user_is_upserted_without_membership(onboarding) -> None:
    service, factory = onboarding
    first_id = await register(service, 200, "Max")
    second_id = await service.register_telegram_user(TelegramProfile(200, "max", "Maxim", None))
    assert first_id == second_id
    assert await service.list_clubs(200) == ()
    async with factory() as session:
        user = await session.get(TelegramUser, first_id)
        assert user.username == "max"
        assert user.first_name == "Maxim"
        assert await session.scalar(select(func.count(Player.id))) == 0


@pytest.mark.asyncio
async def test_existing_membership_opens_club_menu(onboarding) -> None:
    service, factory = onboarding
    club_id, _ = await seed_admin(factory)
    clubs = await service.list_clubs(100)
    assert len(clubs) == 1
    assert clubs[0].id == club_id
    assert clubs[0].role is MembershipRole.ADMIN
    assert clubs[0].player_name == "Admin"


@pytest.mark.asyncio
async def test_invalid_expired_and_exhausted_invites(onboarding) -> None:
    service, factory = onboarding
    club_id, _ = await seed_admin(factory)
    await register(service, 200)
    with pytest.raises(MembershipError) as error:
        await service.preview_invite("does-not-exist")
    assert_error(MembershipErrorCode.INVITE_INVALID, error)

    inactive = await seed_invite(factory, club_id, is_active=False)
    expired = await seed_invite(factory, club_id, expires_at=datetime.now(timezone.utc) - timedelta(days=1))
    exhausted = await seed_invite(factory, club_id, max_uses=1, uses_count=1)
    for invitation, expected in (
        (inactive, MembershipErrorCode.INVITE_INVALID),
        (expired, MembershipErrorCode.INVITE_EXPIRED),
        (exhausted, MembershipErrorCode.INVITE_EXHAUSTED),
    ):
        with pytest.raises(MembershipError) as error:
            await service.preview_invite(invitation.token)
        assert_error(expected, error)
        with pytest.raises(MembershipError) as error:
            await service.join_invite(200, invitation.id)
        assert_error(expected, error)


@pytest.mark.asyncio
async def test_join_invite_and_create_new_player(onboarding) -> None:
    service, factory = onboarding
    club_id, _ = await seed_admin(factory)
    token = await service.create_invite(100, club_id)
    invitation = await service.preview_invite(token)
    assert invitation.club_name == "Saturday Club"
    user_id = await register(service, 200, "Max")
    joined = await service.join_invite(200, invitation.id)
    assert joined.player_name is None
    created = await service.create_new_player(200, club_id)
    assert created.player_name == "Max"
    async with factory() as session:
        player = await session.scalar(
            select(Player).where(Player.club_id == club_id, Player.telegram_user_id == user_id)
        )
        assert player is not None
        assert player.id != user_id
    with pytest.raises(MembershipError) as error:
        await service.create_new_player(200, club_id)
    assert_error(MembershipErrorCode.ALREADY_LINKED, error)


@pytest.mark.asyncio
async def test_historical_player_link_requires_admin_approval(onboarding) -> None:
    service, factory = onboarding
    club_id, _ = await seed_admin(factory)
    invite = await seed_invite(factory, club_id)
    async with factory() as session, session.begin():
        historical = Player(club_id=club_id, display_name="Historical Max", is_active=False)
        session.add(historical)
        await session.flush()
        historical_id = historical.id
    user_id = await register(service, 200, "Max")
    await service.join_invite(200, invite.id)
    choices = await service.list_unlinked_players(200, club_id)
    assert [choice.id for choice in choices] == [historical_id]
    request_id = await service.request_player_link(200, historical_id)
    async with factory() as session:
        assert (await session.get(Player, historical_id)).telegram_user_id is None
    assert await service.request_player_link(200, historical_id) == request_id
    assert (await service.get_club(200, club_id)).has_pending_link
    assert len(await service.list_pending_links(100, club_id)) == 1

    with pytest.raises(MembershipError) as error:
        await service.review_player_link(200, request_id, approve=True)
    assert_error(MembershipErrorCode.NOT_ADMIN, error)

    async with factory() as session, session.begin():
        other_club = Club(name="Other club", timezone="UTC")
        other_admin = TelegramUser(telegram_user_id=300, first_name="Other admin")
        session.add_all([other_club, other_admin])
        await session.flush()
        session.add_all(
            [
                Player(
                    club_id=other_club.id,
                    display_name="Other admin",
                    telegram_user_id=other_admin.id,
                ),
                ClubMembership(
                    club_id=other_club.id,
                    telegram_user_id=other_admin.id,
                    role=MembershipRole.ADMIN,
                    status=MembershipStatus.ACTIVE,
                ),
            ]
        )
    with pytest.raises(MembershipError) as error:
        await service.review_player_link(300, request_id, approve=True)
    assert_error(MembershipErrorCode.NOT_MEMBER, error)

    assert await service.review_player_link(100, request_id, approve=True) is PlayerLinkStatus.APPROVED
    async with factory() as session:
        player = await session.get(Player, historical_id)
        request = await session.get(PlayerLinkRequest, request_id)
        audit = await session.scalar(
            select(AuditLog).where(AuditLog.entity_id == historical_id, AuditLog.action == "PLAYER_LINK")
        )
        assert player.telegram_user_id == user_id
        assert player.is_active
        assert request.status is PlayerLinkStatus.APPROVED
        assert request.reviewed_at.tzinfo is not None
        assert audit is not None
        assert audit.before_data["is_active"] is False
        assert audit.after_data["is_active"] is True
    with pytest.raises(MembershipError) as error:
        await service.review_player_link(100, request_id, approve=True)
    assert_error(MembershipErrorCode.ALREADY_REVIEWED, error)


@pytest.mark.asyncio
async def test_cannot_request_already_bound_or_reserved_player(onboarding) -> None:
    service, factory = onboarding
    club_id, _ = await seed_admin(factory)
    invite = await seed_invite(factory, club_id)
    await register(service, 200)
    await service.join_invite(200, invite.id)
    await register(service, 201, "Other")
    await service.join_invite(201, invite.id)
    await register(service, 202, "Third")
    await service.join_invite(202, invite.id)
    async with factory() as session, session.begin():
        bound = Player(club_id=club_id, display_name="Taken", telegram_user_id=(await service_user_id(session, 201)))
        available = Player(club_id=club_id, display_name="Available")
        session.add_all([bound, available])
        await session.flush()
        bound_id, available_id = bound.id, available.id
    with pytest.raises(MembershipError) as error:
        await service.request_player_link(200, bound_id)
    assert_error(MembershipErrorCode.PLAYER_NOT_AVAILABLE, error)
    await service.request_player_link(200, available_id)
    with pytest.raises(MembershipError) as error:
        await service.request_player_link(202, available_id)
    assert_error(MembershipErrorCode.PLAYER_NOT_AVAILABLE, error)


async def service_user_id(session: AsyncSession, telegram_id: int) -> uuid.UUID:
    return await session.scalar(
        select(TelegramUser.id).where(TelegramUser.telegram_user_id == telegram_id)
    )


@pytest.mark.asyncio
async def test_duplicate_join_does_not_consume_invite_twice(onboarding) -> None:
    service, factory = onboarding
    club_id, _ = await seed_admin(factory)
    invite = await seed_invite(factory, club_id, max_uses=1)
    await register(service, 200)
    await service.join_invite(200, invite.id)
    await service.join_invite(200, invite.id)
    await register(service, 201)
    with pytest.raises(MembershipError) as error:
        await service.join_invite(201, invite.id)
    assert_error(MembershipErrorCode.INVITE_EXHAUSTED, error)
    async with factory() as session:
        count = await session.scalar(
            select(func.count(ClubMembership.id)).where(ClubMembership.club_id == club_id)
        )
        saved_invite = await session.get(InviteToken, invite.id)
        assert count == 2  # admin + one new member
        assert saved_invite.uses_count == 1

    async with factory() as session, session.begin():
        user_id = await service_user_id(session, 200)
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                session.add(
                    ClubMembership(
                        club_id=club_id,
                        telegram_user_id=user_id,
                        role=MembershipRole.PLAYER,
                        status=MembershipStatus.ACTIVE,
                    )
                )
                await session.flush()


@pytest.mark.asyncio
async def test_admin_can_reject_link_and_member_can_retry(onboarding) -> None:
    service, factory = onboarding
    club_id, _ = await seed_admin(factory)
    invite = await seed_invite(factory, club_id)
    await register(service, 200)
    await service.join_invite(200, invite.id)
    async with factory() as session, session.begin():
        player = Player(club_id=club_id, display_name="History")
        session.add(player)
        await session.flush()
        player_id = player.id
    first = await service.request_player_link(200, player_id)
    assert await service.review_player_link(100, first, approve=False) is PlayerLinkStatus.REJECTED
    second = await service.request_player_link(200, player_id)
    assert first != second


@pytest.mark.asyncio
async def test_only_admin_can_create_invite_and_options_are_stored(onboarding) -> None:
    service, factory = onboarding
    club_id, _ = await seed_admin(factory)
    invite = await seed_invite(factory, club_id)
    await register(service, 200)
    await service.join_invite(200, invite.id)
    with pytest.raises(MembershipError) as error:
        await service.create_invite(200, club_id)
    assert_error(MembershipErrorCode.NOT_ADMIN, error)
    with pytest.raises(MembershipError) as error:
        await service.get_admin_club(200, club_id)
    assert_error(MembershipErrorCode.NOT_ADMIN, error)
    with pytest.raises(MembershipError) as error:
        await service.create_invite(100, club_id, max_uses=0)
    assert_error(MembershipErrorCode.INVALID_INVITE_OPTIONS, error)
    with pytest.raises(MembershipError) as error:
        await service.create_invite(
            100, club_id, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)
        )
    assert_error(MembershipErrorCode.INVALID_INVITE_OPTIONS, error)
    expires_at = datetime.now(timezone.utc) + timedelta(days=1)
    first = await service.create_invite(100, club_id, expires_at=expires_at, max_uses=2)
    second = await service.create_invite(100, club_id)
    assert first != second
    assert len(first) >= 32
    async with factory() as session:
        saved = await session.scalar(select(InviteToken).where(InviteToken.token == first))
        assert saved.max_uses == 2
        assert saved.expires_at == expires_at


@pytest.mark.asyncio
async def test_blocked_requester_cannot_be_approved(onboarding) -> None:
    service, factory = onboarding
    club_id, _ = await seed_admin(factory)
    invite = await seed_invite(factory, club_id)
    user_id = await register(service, 200)
    await service.join_invite(200, invite.id)
    async with factory() as session, session.begin():
        historical = Player(club_id=club_id, display_name="History")
        session.add(historical)
        await session.flush()
        player_id = historical.id
    request_id = await service.request_player_link(200, player_id)
    async with factory() as session, session.begin():
        membership = await session.scalar(
            select(ClubMembership).where(
                ClubMembership.club_id == club_id,
                ClubMembership.telegram_user_id == user_id,
            )
        )
        membership.status = MembershipStatus.BLOCKED
    with pytest.raises(MembershipError) as error:
        await service.review_player_link(100, request_id, approve=True)
    assert_error(MembershipErrorCode.BLOCKED, error)
    async with factory() as session:
        assert (await session.get(Player, player_id)).telegram_user_id is None


@pytest.mark.asyncio
async def test_pending_link_unique_indexes_reject_competing_requests(onboarding) -> None:
    service, factory = onboarding
    club_id, _ = await seed_admin(factory)
    invite = await seed_invite(factory, club_id)
    first_user_id = await register(service, 200)
    second_user_id = await register(service, 201)
    await service.join_invite(200, invite.id)
    await service.join_invite(201, invite.id)
    async with factory() as session, session.begin():
        first_player = Player(club_id=club_id, display_name="History 1")
        second_player = Player(club_id=club_id, display_name="History 2")
        session.add_all([first_player, second_player])
        await session.flush()
        first_player_id, second_player_id = first_player.id, second_player.id
    await service.request_player_link(200, first_player_id)

    for user_id, player_id in (
        (first_user_id, second_player_id),
        (second_user_id, first_player_id),
    ):
        async with factory() as session, session.begin():
            with pytest.raises(IntegrityError):
                async with session.begin_nested():
                    session.add(
                        PlayerLinkRequest(
                            club_id=club_id,
                            telegram_user_id=user_id,
                            player_id=player_id,
                            status=PlayerLinkStatus.PENDING,
                        )
                    )
                    await session.flush()
