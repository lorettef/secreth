"""Persisted emergency requests, current presence, authorization, and PostgreSQL races."""

import asyncio
import os
import uuid
from dataclasses import dataclass
from unittest.mock import patch

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import (
    AuditLog, Club, ClubMembership, GameSession, Player, SessionParticipant,
    StatisticianConfirmation, StatisticianRecovery, TelegramUser,
)
from app.domain.enums import MembershipRole, MembershipStatus, RecoveryStatus
from app.services.sessions import SessionError, SessionErrorCode, SessionService


@pytest.fixture(scope="session")
def test_database_url():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a dedicated PostgreSQL test database")
    parsed = make_url(url)
    if parsed.drivername != "postgresql+asyncpg" or not parsed.database.endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must point to a PostgreSQL database ending in _test")
    with patch.dict(os.environ, {"DATABASE_URL": url}):
        command.upgrade(Config("alembic.ini"), "head")
    return url


@dataclass
class Evening:
    service: SessionService
    factory: async_sessionmaker
    club: uuid.UUID
    session: uuid.UUID
    players: tuple[uuid.UUID, ...]
    users: tuple[uuid.UUID, ...]
    telegram: tuple[int, ...]

    async def request(self, *, initiator=1, candidate=2, request_id=None):
        return await self.service.request_recovery(
            self.telegram[initiator], self.session, self.players[candidate], request_id or uuid.uuid4(),
            expected_statistician_id=self.players[0])


async def seed(factory):
    service = SessionService(factory)
    async with factory() as db, db.begin():
        club = Club(name="Recovery Club", timezone="Asia/Yakutsk")
        db.add(club)
        await db.flush()
        players, users, telegram = [], [], []
        for index in range(7):
            telegram_id = uuid.uuid4().int % 10_000_000_000
            user = TelegramUser(telegram_user_id=telegram_id, first_name=f"User {index}")
            db.add(user)
            await db.flush()
            player = Player(club_id=club.id, telegram_user_id=user.id, display_name=f"Player {index}")
            db.add(player)
            await db.flush()
            db.add(ClubMembership(club_id=club.id, telegram_user_id=user.id,
                                 role=MembershipRole.ADMIN if index == 6 else MembershipRole.PLAYER,
                                 status=MembershipStatus.ACTIVE))
            players.append(player.id)
            users.append(user.id)
            telegram.append(telegram_id)
    view, _ = await service.start(telegram[0], club.id, tuple(players[:6]))
    return Evening(service, factory, club.id, view.id, tuple(players), tuple(users), tuple(telegram))


@pytest_asyncio.fixture
async def evening(test_database_url):
    engine = create_async_engine(test_database_url)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        factory = async_sessionmaker(bind=connection, expire_on_commit=False,
                                     join_transaction_mode="create_savepoint")
        yield await seed(factory)
        await transaction.rollback()
    await engine.dispose()


@pytest_asyncio.fixture
async def committed_evening(test_database_url):
    engine = create_async_engine(test_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    data = await seed(factory)
    try:
        yield data
    finally:
        async with factory() as db, db.begin():
            requests = select(StatisticianRecovery.id).where(StatisticianRecovery.session_id == data.session)
            await db.execute(delete(StatisticianConfirmation).where(StatisticianConfirmation.recovery_id.in_(requests)))
            await db.execute(delete(StatisticianRecovery).where(StatisticianRecovery.session_id == data.session))
            await db.execute(delete(AuditLog).where(AuditLog.club_id == data.club))
            await db.execute(delete(SessionParticipant).where(SessionParticipant.session_id == data.session))
            await db.execute(delete(GameSession).where(GameSession.id == data.session))
            await db.execute(delete(ClubMembership).where(ClubMembership.club_id == data.club))
            await db.execute(delete(Player).where(Player.club_id == data.club))
            await db.execute(delete(TelegramUser).where(TelegramUser.id.in_(data.users)))
            await db.execute(delete(Club).where(Club.id == data.club))
        await engine.dispose()


async def assert_statistician(data, index):
    async with data.factory() as db:
        assert (await db.get(GameSession, data.session)).statistician_player_id == data.players[index]


@pytest.mark.asyncio
async def test_player_initiates_and_two_other_players_apply_with_one_audit(evening):
    data = evening
    request = await data.request()
    assert request.status is RecoveryStatus.PENDING and request.confirmations == 0
    assert not request.can_confirm
    first = await data.service.confirm_recovery(data.telegram[3], request.id)
    assert first.status is RecoveryStatus.PENDING and first.confirmations == 1
    await assert_statistician(data, 0)
    second = await data.service.confirm_recovery(data.telegram[4], request.id)
    assert second.status is RecoveryStatus.APPLIED and second.confirmations == 2
    await assert_statistician(data, 2)
    assert not (await data.service.view(data.telegram[0], data.session)).can_manage
    assert (await data.service.view(data.telegram[2], data.session)).can_manage
    async with data.factory() as db:
        logs = (await db.scalars(select(AuditLog).where(AuditLog.entity_id == data.session))).all()
        assert len(logs) == 1 and logs[0].action == "STATISTICIAN_CHANGE"
        assert logs[0].before_data == {"statistician_player_id": str(data.players[0])}
        assert logs[0].after_data == {"statistician_player_id": str(data.players[2]), "recovery_id": str(request.id)}


@pytest.mark.asyncio
async def test_duplicate_confirmation_and_process_restart_do_not_add_votes(evening):
    data = evening
    request = await data.request()
    first = await data.service.confirm_recovery(data.telegram[3], request.id)
    restarted = SessionService(data.factory)
    second = await restarted.confirm_recovery(data.telegram[3], request.id)
    assert first.confirmations == second.confirmations == 1
    assert (await restarted.recovery_view(data.telegram[4], request.id)).can_confirm
    await assert_statistician(data, 0)
    async with data.factory() as db:
        assert await db.scalar(select(func.count(StatisticianConfirmation.id))) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("index", [1, 2])
async def test_initiator_and_candidate_cannot_supply_required_confirmations(evening, index):
    data = evening
    request = await data.request()
    with pytest.raises(SessionError) as error:
        await data.service.confirm_recovery(data.telegram[index], request.id)
    assert error.value.code is SessionErrorCode.CONFIRMATION_NOT_ALLOWED
    assert (await data.service.recovery_view(data.telegram[3], request.id)).confirmations == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("candidate", [0, 6])
async def test_current_statistician_and_absent_candidate_rejected(evening, candidate):
    with pytest.raises(SessionError) as error:
        await evening.request(candidate=candidate)
    assert error.value.code is SessionErrorCode.TRANSFER_NOT_AVAILABLE


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("is_active", False), ("telegram_user_id", None)])
async def test_inactive_or_unlinked_candidate_rejected(evening, field, value):
    data = evening
    async with data.factory() as db, db.begin():
        setattr(await db.get(Player, data.players[2]), field, value)
    assert data.players[2] not in {p.id for p in await data.service.recovery_choices(data.telegram[1], data.session)}
    with pytest.raises(SessionError):
        await data.request()


@pytest.mark.asyncio
async def test_absent_player_cannot_initiate(evening):
    data = evening
    await data.service.remove_player(data.telegram[0], data.session, data.players[1])
    with pytest.raises(SessionError) as error:
        await data.request()
    assert error.value.code is SessionErrorCode.NO_ACCESS


@pytest.mark.asyncio
@pytest.mark.parametrize("index,rejoin", [(2, False), (2, True), (1, False)])
async def test_candidate_or_initiator_departure_invalidates_request(evening, index, rejoin):
    data = evening
    request = await data.request()
    await data.service.remove_player(data.telegram[0], data.session, data.players[index])
    if rejoin:
        await data.service.add_player(data.telegram[0], data.session, data.players[index])
    with pytest.raises(SessionError) as error:
        await data.service.confirm_recovery(data.telegram[3], request.id)
    assert error.value.code is SessionErrorCode.STALE
    await assert_statistician(data, 0)


@pytest.mark.asyncio
async def test_departed_confirmer_cannot_confirm(evening):
    data = evening
    request = await data.request()
    await data.service.remove_player(data.telegram[0], data.session, data.players[3])
    with pytest.raises(SessionError) as error:
        await data.service.confirm_recovery(data.telegram[3], request.id)
    assert error.value.code is SessionErrorCode.CONFIRMATION_NOT_ALLOWED


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked", [False, True])
async def test_old_vote_is_revalidated_when_second_vote_arrives(evening, blocked):
    data = evening
    request = await data.request()
    await data.service.confirm_recovery(data.telegram[3], request.id)
    if blocked:
        async with data.factory() as db, db.begin():
            membership = await db.scalar(select(ClubMembership).where(
                ClubMembership.telegram_user_id == data.users[3]))
            membership.status = MembershipStatus.BLOCKED
    else:
        await data.service.remove_player(data.telegram[0], data.session, data.players[3])
        await data.service.add_player(data.telegram[0], data.session, data.players[3])
    second = await data.service.confirm_recovery(data.telegram[4], request.id)
    assert second.status is RecoveryStatus.PENDING and second.confirmations == 1
    await assert_statistician(data, 0)
    third = await data.service.confirm_recovery(data.telegram[5], request.id)
    assert third.status is RecoveryStatus.APPLIED


@pytest.mark.asyncio
async def test_cross_club_user_and_candidate_cannot_bypass_permissions(evening):
    data = evening
    request = await data.request()
    async with data.factory() as db, db.begin():
        club = Club(name="Other", timezone="UTC")
        user = TelegramUser(telegram_user_id=uuid.uuid4().int % 10_000_000_000)
        db.add_all([club, user])
        await db.flush()
        player = Player(club_id=club.id, telegram_user_id=user.id, display_name="Outsider")
        db.add(player)
        db.add(ClubMembership(club_id=club.id, telegram_user_id=user.id, role=MembershipRole.ADMIN,
                              status=MembershipStatus.ACTIVE))
        await db.flush()
    for operation in (data.service.confirm_recovery(user.telegram_user_id, request.id),
                      data.service.recovery_view(user.telegram_user_id, request.id),
                      data.service.request_recovery(user.telegram_user_id, data.session, data.players[2],
                                                    uuid.uuid4(), expected_statistician_id=data.players[0])):
        with pytest.raises(SessionError) as error:
            await operation
        assert error.value.code is SessionErrorCode.NO_ACCESS
    with pytest.raises(SessionError) as error:
        await data.service.request_recovery(data.telegram[1], data.session, player.id, uuid.uuid4(),
                                            expected_statistician_id=data.players[0])
    assert error.value.code is SessionErrorCode.TRANSFER_NOT_AVAILABLE


@pytest.mark.asyncio
async def test_finished_session_rejects_requests_and_confirmations_even_for_admin(evening):
    data = evening
    request = await data.request()
    await data.service.finish(data.telegram[0], data.session)
    with pytest.raises(SessionError) as error:
        await data.service.confirm_recovery(data.telegram[3], request.id)
    assert error.value.code is SessionErrorCode.SESSION_FINISHED
    with pytest.raises(SessionError) as error:
        await data.request(initiator=6)
    assert error.value.code is SessionErrorCode.SESSION_FINISHED


@pytest.mark.asyncio
async def test_normal_transfer_invalidates_request_even_if_statistician_returns(evening):
    data = evening
    request = await data.request()
    await data.service.transfer(data.telegram[0], data.session, data.players[4])
    await data.service.transfer(data.telegram[4], data.session, data.players[0])
    with pytest.raises(SessionError) as error:
        await data.service.confirm_recovery(data.telegram[3], request.id)
    assert error.value.code is SessionErrorCode.STALE


@pytest.mark.asyncio
async def test_admin_overrides_without_presence_or_confirmations_and_old_logs_unchanged(evening):
    data = evening
    old_id = uuid.uuid4()
    async with data.factory() as db, db.begin():
        db.add(AuditLog(id=old_id, club_id=data.club, entity_type="GameSession", entity_id=data.session,
                        action="STATISTICIAN_TRANSFER", before_data={"historical": True}))
    request_id = uuid.uuid4()
    result = await data.request(initiator=6, request_id=request_id)
    assert result.status is RecoveryStatus.APPLIED and result.confirmations == 0
    replay = await data.request(initiator=6, request_id=request_id)
    assert replay.id == result.id and replay.status is RecoveryStatus.APPLIED
    await assert_statistician(data, 2)
    async with data.factory() as db:
        assert (await db.get(AuditLog, old_id)).action == "STATISTICIAN_TRANSFER"
        assert await db.scalar(select(func.count(AuditLog.id)).where(AuditLog.action == "STATISTICIAN_CHANGE")) == 1


@pytest.mark.asyncio
async def test_request_replay_cannot_change_candidate_or_owner(evening):
    data = evening
    request_id = uuid.uuid4()
    first = await data.request(request_id=request_id)
    assert (await data.request(request_id=request_id)).id == first.id
    for initiator, candidate in ((1, 3), (3, 2)):
        with pytest.raises(SessionError) as error:
            await data.request(initiator=initiator, candidate=candidate, request_id=request_id)
        assert error.value.code is SessionErrorCode.STALE


@pytest.mark.asyncio
async def test_database_rejects_duplicate_confirmation_rows(evening):
    data = evening
    request = await data.request()
    await data.service.confirm_recovery(data.telegram[3], request.id)
    async with data.factory() as db:
        vote = await db.scalar(select(StatisticianConfirmation).where(StatisticianConfirmation.recovery_id == request.id))
        with pytest.raises(IntegrityError):
            async with db.begin_nested():
                db.add(StatisticianConfirmation(recovery_id=request.id, telegram_user_id=vote.telegram_user_id,
                                               player_id=vote.player_id, presence_id=vote.presence_id))
                await db.flush()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["finish", "transfer", "add_player", "remove_player"])
async def test_admin_old_intent_rejected_after_statistician_change(evening, operation):
    data = evening
    await data.service.transfer(data.telegram[0], data.session, data.players[4])
    method = getattr(data.service, operation)
    args = (data.telegram[6], data.session)
    if operation != "finish":
        args += (data.players[5],)
    with pytest.raises(SessionError) as error:
        await method(*args, expected_statistician_id=data.players[0])
    assert error.value.code is SessionErrorCode.STALE


@pytest.mark.asyncio
async def test_concurrent_confirmations_and_replays_apply_once(committed_evening):
    data = committed_evening
    request = await data.request()
    await data.service.confirm_recovery(data.telegram[3], request.id)
    results = await asyncio.gather(*(data.service.confirm_recovery(data.telegram[index], request.id)
                                   for index in (4, 4, 5)))
    assert all(result.status is RecoveryStatus.APPLIED for result in results)
    await assert_statistician(data, 2)
    async with data.factory() as db:
        assert await db.scalar(select(func.count(AuditLog.id)).where(AuditLog.entity_id == data.session)) == 1
        assert await db.scalar(select(func.count(StatisticianConfirmation.id)).where(
            StatisticianConfirmation.recovery_id == request.id)) == 2


@pytest.mark.asyncio
async def test_competing_requests_only_one_can_apply(committed_evening):
    data = committed_evening
    first = await data.request()
    other = await data.request(candidate=5)
    await data.service.confirm_recovery(data.telegram[3], first.id)
    await data.service.confirm_recovery(data.telegram[3], other.id)
    results = await asyncio.gather(data.service.confirm_recovery(data.telegram[4], first.id),
                                   data.service.confirm_recovery(data.telegram[4], other.id), return_exceptions=True)
    assert sum(not isinstance(result, Exception) for result in results) == 1
    error = next(result for result in results if isinstance(result, Exception))
    assert isinstance(error, SessionError) and error.code is SessionErrorCode.STALE
    async with data.factory() as db:
        assert await db.scalar(select(func.count(AuditLog.id)).where(AuditLog.entity_id == data.session)) == 1
