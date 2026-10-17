"""Invite-based Mini App onboarding against PostgreSQL."""

import asyncio
import logging
import os
import time
import uuid
from unittest.mock import patch

import httpx
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.app import create_app
from app.config import Settings
from app.db.models import Club, ClubMembership, InviteToken, Player, TelegramUser
from app.domain.enums import MembershipRole, MembershipStatus
from app.services.bootstrap import ClubBootstrapService
from test_telegram_auth import BOT_TOKEN, signed_init_data


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


def authorization(telegram_id: int) -> dict[str, str]:
    raw = signed_init_data(telegram_id=telegram_id, auth_date=int(time.time()))
    return {"Authorization": f"tma {raw}"}


def registration_payload(token: str, nickname: str = "newmember") -> dict[str, str]:
    return {
        "invite_token": token,
        "first_name": "  Иван\t",
        "last_name": " Петров  ",
        "school_nickname": nickname,
    }


async def seed_invite(
    factory,
    club_id,
    creator_id,
    *,
    token: str | None = None,
    active: bool = True,
) -> InviteToken:
    async with factory() as session, session.begin():
        invitation = InviteToken(
            club_id=club_id,
            token=token or f"invite-{uuid.uuid4().hex}",
            created_by=creator_id,
            is_active=active,
            max_uses=1,
        )
        session.add(invitation)
        await session.flush()
        return invitation


@pytest_asyncio.fixture
async def onboarding_api(test_database_url: str):
    engine = create_async_engine(test_database_url)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        factory = async_sessionmaker(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        async with factory() as session, session.begin():
            club = Club(name="Onboarding club", timezone="Asia/Yakutsk")
            other_club = Club(name="Other club", timezone="UTC")
            admin_user = TelegramUser(telegram_user_id=96100, first_name="Admin")
            session.add_all((club, other_club, admin_user))
            await session.flush()
            admin = Player(
                club_id=club.id,
                telegram_user_id=admin_user.id,
                display_name="Admin",
            )
            session.add(admin)
            await session.flush()
            session.add(
                ClubMembership(
                    club_id=club.id,
                    telegram_user_id=admin_user.id,
                    role=MembershipRole.ADMIN,
                    status=MembershipStatus.ACTIVE,
                )
            )
        settings = Settings(
            _env_file=None,
            bot_token=BOT_TOKEN,
            database_url=test_database_url,
            telegram_init_data_max_age_seconds=3600,
        )
        application = create_app(settings, session_factory=factory)
        transport = httpx.ASGITransport(app=application, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport, base_url="https://mini-app.test"
        ) as client:
            yield client, factory, club, other_club, admin
        await transaction.rollback()
    await engine.dispose()


@pytest.mark.asyncio
async def test_registers_profile_and_consumes_regular_invite(onboarding_api) -> None:
    client, factory, club, _, admin = onboarding_api
    invitation = await seed_invite(factory, club.id, admin.id)

    response = await client.post(
        "/api/v1/onboarding/register",
        headers=authorization(96101),
        json=registration_payload(invitation.token, "SchoolNick"),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["registration_state"] == "REGISTERED"
    assert body["club_id"] == str(club.id)
    assert body["membership_role"] == "PLAYER"
    assert body["membership_status"] == "ACTIVE"
    assert body["profile"] == {
        "id": body["profile"]["id"],
        "display_name": "Иван Петров",
        "first_name": "Иван",
        "last_name": "Петров",
        "school_nickname": "SchoolNick",
    }
    async with factory() as session:
        user = await session.scalar(
            select(TelegramUser).where(TelegramUser.telegram_user_id == 96101)
        )
        membership = await session.scalar(
            select(ClubMembership).where(
                ClubMembership.club_id == club.id,
                ClubMembership.telegram_user_id == user.id,
            )
        )
        player = await session.scalar(
            select(Player).where(
                Player.club_id == club.id,
                Player.telegram_user_id == user.id,
            )
        )
        saved_invite = await session.get(InviteToken, invitation.id)
    assert membership.id == uuid.UUID(body["membership_id"])
    assert membership.role is MembershipRole.PLAYER
    assert player.id == uuid.UUID(body["profile"]["id"])
    assert saved_invite.uses_count == 1
    assert not saved_invite.is_active

    me = await client.get(
        "/api/v1/me",
        params={"club_id": str(club.id)},
        headers=authorization(96101),
    )
    assert me.status_code == 200
    assert me.json()["registration_state"] == "REGISTERED"
    assert me.json()["profile"]["school_nickname"] == "SchoolNick"


@pytest.mark.asyncio
async def test_bootstrap_invite_registers_player_without_owner(onboarding_api) -> None:
    client, factory, _, _, _ = onboarding_api
    bootstrap = ClubBootstrapService(factory)
    club_id = await bootstrap.create_club("Bootstrap HTTP", "UTC")
    token = await bootstrap.create_initial_invite(club_id)

    response = await client.post(
        "/api/v1/onboarding/register",
        headers=authorization(96102),
        json=registration_payload(token, "bootstrapnick"),
    )

    assert response.status_code == 201
    assert response.json()["club_id"] == str(club_id)
    assert response.json()["membership_role"] == "PLAYER"
    async with factory() as session:
        invitation = await session.scalar(
            select(InviteToken).where(InviteToken.token == token)
        )
        owner_count = await session.scalar(
            select(func.count(ClubMembership.id)).where(
                ClubMembership.club_id == club_id,
                ClubMembership.role == MembershipRole.OWNER,
            )
        )
    assert invitation.is_bootstrap
    assert invitation.expires_at is None
    assert invitation.uses_count == 1
    assert not invitation.is_active
    assert owner_count == 0

    assignment = await bootstrap.assign_owner(club_id, 96102)
    assert assignment.changed


@pytest.mark.asyncio
async def test_reused_revoked_and_unknown_invites_are_rejected_safely(
    onboarding_api, caplog: pytest.LogCaptureFixture
) -> None:
    client, factory, club, _, admin = onboarding_api
    used = await seed_invite(factory, club.id, admin.id)
    revoked = await seed_invite(factory, club.id, admin.id, active=False)
    first = await client.post(
        "/api/v1/onboarding/register",
        headers=authorization(96103),
        json=registration_payload(used.token, "firstnick"),
    )
    assert first.status_code == 201

    with caplog.at_level(logging.INFO):
        for telegram_id, token in (
            (96104, used.token),
            (96105, revoked.token),
            (96106, "unknown-sensitive-invite"),
        ):
            response = await client.post(
                "/api/v1/onboarding/register",
                headers=authorization(telegram_id),
                json=registration_payload(token, f"nick{telegram_id}"),
            )
            assert response.status_code == 400
            assert response.json() == {"detail": "INVITE_INVALID"}
            assert token not in response.text
    assert used.token not in caplog.text
    assert revoked.token not in caplog.text
    assert "unknown-sensitive-invite" not in caplog.text


@pytest.mark.asyncio
async def test_nickname_conflict_and_invalid_profile_roll_back_everything(
    onboarding_api,
) -> None:
    client, factory, club, _, admin = onboarding_api
    first_invite = await seed_invite(factory, club.id, admin.id)
    second_invite = await seed_invite(factory, club.id, admin.id)
    invalid_invite = await seed_invite(factory, club.id, admin.id)
    assert (
        await client.post(
            "/api/v1/onboarding/register",
            headers=authorization(96107),
            json=registration_payload(first_invite.token, "SameNick"),
        )
    ).status_code == 201

    conflict = await client.post(
        "/api/v1/onboarding/register",
        headers=authorization(96108),
        json=registration_payload(second_invite.token, "samenick"),
    )
    invalid = await client.post(
        "/api/v1/onboarding/register",
        headers=authorization(96109),
        json={
            **registration_payload(invalid_invite.token, "validnick"),
            "last_name": "　",
        },
    )
    assert conflict.status_code == 409
    assert conflict.json() == {"detail": "SCHOOL_NICKNAME_CONFLICT"}
    assert invalid.status_code == 422
    assert invalid.json() == {"detail": "LAST_NAME_REQUIRED"}

    async with factory() as session:
        for telegram_id, invite_id in (
            (96108, second_invite.id),
            (96109, invalid_invite.id),
        ):
            assert (
                await session.scalar(
                    select(TelegramUser.id).where(
                        TelegramUser.telegram_user_id == telegram_id
                    )
                )
                is None
            )
            assert (await session.get(InviteToken, invite_id)).uses_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("member_status", "telegram_id"),
    [(MembershipStatus.BLOCKED, 96110), (MembershipStatus.INACTIVE, 96111)],
)
async def test_existing_restricted_member_cannot_register_again(
    onboarding_api, member_status, telegram_id
) -> None:
    client, factory, club, _, admin = onboarding_api
    invitation = await seed_invite(factory, club.id, admin.id)
    async with factory() as session, session.begin():
        user = TelegramUser(telegram_user_id=telegram_id)
        session.add(user)
        await session.flush()
        session.add(
            ClubMembership(
                club_id=club.id,
                telegram_user_id=user.id,
                role=MembershipRole.PLAYER,
                status=member_status,
            )
        )

    response = await client.post(
        "/api/v1/onboarding/register",
        headers=authorization(telegram_id),
        json=registration_payload(invitation.token, f"restricted{telegram_id}"),
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "ONBOARDING_ACCESS_DENIED"}
    async with factory() as session:
        assert (await session.get(InviteToken, invitation.id)).uses_count == 0
        assert (
            await session.scalar(
                select(func.count(Player.id)).where(Player.club_id == club.id)
            )
            == 1
        )  # existing admin only


@pytest.mark.asyncio
async def test_existing_active_membership_is_not_overwritten_or_reconsumed(
    onboarding_api,
) -> None:
    client, factory, club, _, admin = onboarding_api
    invitation = await seed_invite(factory, club.id, admin.id)
    async with factory() as session, session.begin():
        user = TelegramUser(telegram_user_id=96112)
        session.add(user)
        await session.flush()
        membership = ClubMembership(
            club_id=club.id,
            telegram_user_id=user.id,
            role=MembershipRole.ADMIN,
            status=MembershipStatus.ACTIVE,
        )
        session.add(membership)
        await session.flush()
        membership_id = membership.id

    response = await client.post(
        "/api/v1/onboarding/register",
        headers=authorization(96112),
        json=registration_payload(invitation.token, "existingmember"),
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "MEMBERSHIP_EXISTS"}
    async with factory() as session:
        saved = await session.get(ClubMembership, membership_id)
        assert saved.role is MembershipRole.ADMIN
        assert (await session.get(InviteToken, invitation.id)).uses_count == 0


@pytest.mark.asyncio
async def test_signed_identity_cannot_be_replaced_by_request_data(
    onboarding_api,
) -> None:
    client, factory, club, _, admin = onboarding_api
    invitation = await seed_invite(factory, club.id, admin.id)
    payload = {
        **registration_payload(invitation.token, "trustedid"),
        "telegram_id": 999999,
    }

    response = await client.post(
        "/api/v1/onboarding/register",
        headers=authorization(96113),
        json=payload,
    )

    assert response.status_code == 201
    async with factory() as session:
        assert (
            await session.scalar(
                select(TelegramUser.id).where(TelegramUser.telegram_user_id == 96113)
            )
            is not None
        )
        assert (
            await session.scalar(
                select(TelegramUser.id).where(TelegramUser.telegram_user_id == 999999)
            )
            is None
        )


@pytest.mark.asyncio
async def test_invitation_registers_only_in_its_own_club(onboarding_api) -> None:
    client, factory, _, other_club, _ = onboarding_api
    async with factory() as session, session.begin():
        other_admin_user = TelegramUser(telegram_user_id=96114)
        session.add(other_admin_user)
        await session.flush()
        other_admin = Player(
            club_id=other_club.id,
            telegram_user_id=other_admin_user.id,
            display_name="Other admin",
        )
        session.add(other_admin)
        await session.flush()
    invitation = await seed_invite(factory, other_club.id, other_admin.id)

    response = await client.post(
        "/api/v1/onboarding/register",
        headers=authorization(96115),
        json=registration_payload(invitation.token, "otherclubmember"),
    )

    assert response.status_code == 201
    assert response.json()["club_id"] == str(other_club.id)
    async with factory() as session:
        memberships = (
            await session.scalars(
                select(ClubMembership)
                .join(TelegramUser)
                .where(TelegramUser.telegram_user_id == 96115)
            )
        ).all()
    assert [membership.club_id for membership in memberships] == [other_club.id]


@pytest.mark.asyncio
async def test_profile_required_can_be_completed_without_invite(onboarding_api) -> None:
    client, factory, club, _, _ = onboarding_api
    async with factory() as session, session.begin():
        user = TelegramUser(telegram_user_id=96116)
        session.add(user)
        await session.flush()
        session.add(
            ClubMembership(
                club_id=club.id,
                telegram_user_id=user.id,
                role=MembershipRole.PLAYER,
                status=MembershipStatus.ACTIVE,
            )
        )

    before = await client.get(
        "/api/v1/me",
        params={"club_id": str(club.id)},
        headers=authorization(96116),
    )
    completed = await client.post(
        "/api/v1/onboarding/profile",
        headers=authorization(96116),
        json={"club_id": str(club.id), **registration_payload("unused", "profiledone")},
    )
    assert before.json()["registration_state"] == "PROFILE_REQUIRED"
    assert completed.status_code == 200
    assert completed.json()["registration_state"] == "REGISTERED"
    assert completed.json()["profile"]["school_nickname"] == "profiledone"

    repeated = await client.post(
        "/api/v1/onboarding/profile",
        headers=authorization(96116),
        json={"club_id": str(club.id), **registration_payload("unused", "changed")},
    )
    assert repeated.status_code == 409
    assert repeated.json() == {"detail": "PROFILE_ALREADY_COMPLETE"}


@pytest.mark.asyncio
async def test_profile_completion_cannot_target_another_identity_or_club(
    onboarding_api,
) -> None:
    client, factory, club, other_club, _ = onboarding_api
    async with factory() as session, session.begin():
        owner = TelegramUser(telegram_user_id=96117)
        attacker = TelegramUser(telegram_user_id=96118)
        session.add_all((owner, attacker))
        await session.flush()
        session.add(
            ClubMembership(
                club_id=club.id,
                telegram_user_id=owner.id,
                role=MembershipRole.PLAYER,
                status=MembershipStatus.ACTIVE,
            )
        )

    for telegram_id, club_id in ((96118, club.id), (96117, other_club.id)):
        response = await client.post(
            "/api/v1/onboarding/profile",
            headers=authorization(telegram_id),
            json={"club_id": str(club_id), **registration_payload("unused", "attack")},
        )
        assert response.status_code == 403


@pytest.mark.asyncio
async def test_concurrent_use_of_one_invite_creates_one_member(
    test_database_url: str,
) -> None:
    engine = create_async_engine(test_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    club_id = None
    admin_user_id = None
    invite_id = None
    telegram_ids = (
        uuid.uuid4().int % 1_000_000_000 + 2_000_000_000,
        uuid.uuid4().int % 1_000_000_000 + 3_000_000_000,
    )
    try:
        async with factory() as session, session.begin():
            club = Club(name="Concurrent HTTP onboarding", timezone="UTC")
            admin_user = TelegramUser(
                telegram_user_id=uuid.uuid4().int % 1_000_000_000 + 4_000_000_000
            )
            session.add_all((club, admin_user))
            await session.flush()
            admin = Player(
                club_id=club.id,
                telegram_user_id=admin_user.id,
                display_name="Concurrent admin",
            )
            session.add(admin)
            await session.flush()
            invitation = InviteToken(
                club_id=club.id,
                token=f"concurrent-{uuid.uuid4().hex}",
                created_by=admin.id,
                max_uses=1,
            )
            session.add_all(
                (
                    ClubMembership(
                        club_id=club.id,
                        telegram_user_id=admin_user.id,
                        role=MembershipRole.ADMIN,
                        status=MembershipStatus.ACTIVE,
                    ),
                    invitation,
                )
            )
            await session.flush()
            club_id, admin_user_id, invite_id = club.id, admin_user.id, invitation.id
            token = invitation.token

        settings = Settings(
            _env_file=None,
            bot_token=BOT_TOKEN,
            database_url=test_database_url,
        )
        application = create_app(settings, session_factory=factory)
        transport = httpx.ASGITransport(app=application, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport, base_url="https://mini-app.test"
        ) as client:
            responses = await asyncio.wait_for(
                asyncio.gather(
                    *(
                        client.post(
                            "/api/v1/onboarding/register",
                            headers=authorization(telegram_id),
                            json=registration_payload(token, f"concurrent{index}"),
                        )
                        for index, telegram_id in enumerate(telegram_ids)
                    )
                ),
                timeout=10,
            )
        assert sorted(response.status_code for response in responses) == [201, 400]
        async with factory() as session:
            invitation = await session.get(InviteToken, invite_id)
            assert invitation.uses_count == 1
            assert (
                await session.scalar(
                    select(func.count(ClubMembership.id)).where(
                        ClubMembership.club_id == club_id
                    )
                )
                == 2
            )  # admin plus one successful registration
    finally:
        if club_id is not None:
            async with factory() as session, session.begin():
                await session.execute(
                    delete(InviteToken).where(InviteToken.club_id == club_id)
                )
                await session.execute(
                    delete(ClubMembership).where(ClubMembership.club_id == club_id)
                )
                await session.execute(delete(Player).where(Player.club_id == club_id))
                await session.execute(
                    delete(TelegramUser).where(
                        (TelegramUser.id == admin_user_id)
                        | (TelegramUser.telegram_user_id.in_(telegram_ids))
                    )
                )
                await session.execute(delete(Club).where(Club.id == club_id))
        await engine.dispose()
