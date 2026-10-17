"""FastAPI authentication and club-scoped /me behavior on PostgreSQL."""

import logging
import os
import time
from unittest.mock import patch

import httpx
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.app import create_app
from app.config import Settings
from app.db.models import Club, ClubMembership, Player, TelegramUser
from app.domain.enums import MembershipRole, MembershipStatus
from test_telegram_auth import BOT_TOKEN, signed_init_data


@pytest.fixture(scope="session")
def test_database_url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a dedicated PostgreSQL test database")
    with patch.dict(os.environ, {"DATABASE_URL": url}):
        command.upgrade(Config("alembic.ini"), "head")
    return url


@pytest_asyncio.fixture
async def api_data(test_database_url: str):
    engine = create_async_engine(test_database_url)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        factory = async_sessionmaker(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        async with factory() as session, session.begin():
            club = Club(name="API club", timezone="UTC")
            other_club = Club(name="Other API club", timezone="UTC")
            session.add_all([club, other_club])
            await session.flush()
            identities = {}
            for telegram_id, status in (
                (95101, MembershipStatus.ACTIVE),
                (95102, MembershipStatus.INACTIVE),
                (95103, MembershipStatus.BLOCKED),
            ):
                user = TelegramUser(telegram_user_id=telegram_id, first_name="API")
                session.add(user)
                await session.flush()
                player = Player(
                    club_id=club.id,
                    telegram_user_id=user.id,
                    display_name=f"Member {telegram_id}",
                    first_name="Имя",
                    last_name="Фамилия",
                    school_nickname=f"nick{telegram_id}",
                    is_active=status is MembershipStatus.ACTIVE,
                )
                session.add_all(
                    [
                        player,
                        ClubMembership(
                            club_id=club.id,
                            telegram_user_id=user.id,
                            role=MembershipRole.PLAYER,
                            status=status,
                        ),
                    ]
                )
                identities[telegram_id] = player
            no_membership_user = TelegramUser(
                telegram_user_id=95104, first_name="No membership"
            )
            session.add(no_membership_user)
            await session.flush()

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
            yield client, club, other_club
        await transaction.rollback()
    await engine.dispose()


def authorization(telegram_id: int) -> dict[str, str]:
    return {
        "Authorization": f"tma {signed_init_data(telegram_id=telegram_id, auth_date=int(time.time()))}"
    }


@pytest.mark.asyncio
async def test_health_is_public_and_reveals_no_configuration(api_data) -> None:
    client, _, _ = api_data
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "telegram_id,expected_status",
    [(95101, "ACTIVE"), (95102, "INACTIVE")],
)
async def test_active_and_inactive_members_receive_own_profile(
    api_data, telegram_id: int, expected_status: str
) -> None:
    client, club, _ = api_data
    response = await client.get(
        "/api/v1/me",
        params={"club_id": str(club.id)},
        headers=authorization(telegram_id),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["authenticated"] is True
    assert body["telegram_id"] == telegram_id
    assert body["registration_state"] == "REGISTERED"
    assert body["club_id"] == str(club.id)
    assert body["membership_role"] == "PLAYER"
    assert body["membership_status"] == expected_status
    assert body["profile"]["school_nickname"] == f"nick{telegram_id}"


@pytest.mark.asyncio
async def test_blocked_member_is_denied_without_club_data(api_data) -> None:
    client, club, _ = api_data
    response = await client.get(
        "/api/v1/me",
        params={"club_id": str(club.id)},
        headers=authorization(95103),
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "CLUB_ACCESS_DENIED"}
    assert str(club.id) not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("telegram_id", [95104, 95999])
async def test_missing_membership_has_explicit_onboarding_state(
    api_data, telegram_id: int
) -> None:
    client, club, _ = api_data
    response = await client.get(
        "/api/v1/me",
        params={"club_id": str(club.id)},
        headers=authorization(telegram_id),
    )
    assert response.status_code == 200
    assert response.json() == {
        "authenticated": True,
        "telegram_id": telegram_id,
        "registration_state": "NOT_MEMBER",
        "club_id": None,
        "membership_id": None,
        "membership_role": None,
        "membership_status": None,
        "profile": None,
    }


@pytest.mark.asyncio
async def test_other_club_scope_does_not_leak_membership(api_data) -> None:
    client, _, other_club = api_data
    response = await client.get(
        "/api/v1/me",
        params={"club_id": str(other_club.id)},
        headers=authorization(95101),
    )
    assert response.status_code == 200
    assert response.json()["registration_state"] == "NOT_MEMBER"
    assert response.json()["club_id"] is None


@pytest.mark.asyncio
async def test_client_telegram_id_parameter_cannot_replace_signed_identity(
    api_data,
) -> None:
    client, club, _ = api_data
    response = await client.get(
        "/api/v1/me",
        params={"club_id": str(club.id), "telegram_id": 95103},
        headers=authorization(95101),
    )
    assert response.status_code == 200
    assert response.json()["telegram_id"] == 95101
    assert response.json()["membership_status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_auth_failures_do_not_echo_secrets_or_init_data(
    api_data, caplog: pytest.LogCaptureFixture
) -> None:
    client, club, _ = api_data
    raw = signed_init_data(telegram_id=95101, auth_date=int(time.time()))
    tampered = raw.replace("AAExampleQuery", "sensitive-tampered-value")
    with caplog.at_level(logging.INFO):
        response = await client.get(
            "/api/v1/me",
            params={"club_id": str(club.id)},
            headers={"Authorization": f"tma {tampered}"},
        )
    assert response.status_code == 401
    combined = response.text + caplog.text
    assert BOT_TOKEN not in combined
    assert raw not in combined
    assert "sensitive-tampered-value" not in combined


@pytest.mark.asyncio
async def test_missing_authentication_is_predictable(api_data) -> None:
    client, club, _ = api_data
    response = await client.get(
        "/api/v1/me",
        params={"club_id": str(club.id)},
        headers={"X-Telegram-ID": "95101"},
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "TELEGRAM_AUTH_REQUIRED"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("telegram_id", "expected_status"),
    [(95101, "ACTIVE"), (95102, "INACTIVE")],
)
async def test_my_clubs_lists_only_accessible_own_memberships(
    api_data, telegram_id: int, expected_status: str
) -> None:
    client, club, _ = api_data
    response = await client.get(
        "/api/v1/my-clubs",
        headers=authorization(telegram_id),
    )
    assert response.status_code == 200
    assert response.json() == {
        "blocked": False,
        "clubs": [
            {
                "id": str(club.id),
                "name": "API club",
                "membership_role": "PLAYER",
                "membership_status": expected_status,
            }
        ],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("telegram_id", [95103, 95104, 95999])
async def test_my_clubs_hides_blocked_and_does_not_create_membership(
    api_data, telegram_id: int
) -> None:
    client, _, _ = api_data
    response = await client.get(
        "/api/v1/my-clubs",
        params={"telegram_id": 95101},
        headers=authorization(telegram_id),
    )
    assert response.status_code == 200
    assert response.json() == {
        "clubs": [],
        "blocked": telegram_id == 95103,
    }
