"""Access decisions for player, statistician, and admin contexts."""

from dataclasses import replace
from uuid import UUID

import pytest

from app.domain.enums import MembershipRole, MembershipStatus, SessionStatus
from app.domain.permissions import (
    ActorContext,
    SessionContext,
    can_create_game,
    can_edit_game,
    can_manage_club,
    can_manage_current_session,
    can_manage_players,
    can_start_session,
)

CLUB_ID = UUID(int=1)
OTHER_CLUB_ID = UUID(int=2)
PLAYER_ID = UUID(int=3)
OTHER_PLAYER_ID = UUID(int=4)


@pytest.fixture
def player() -> ActorContext:
    return ActorContext(
        club_id=CLUB_ID,
        membership_role=MembershipRole.PLAYER,
        membership_status=MembershipStatus.ACTIVE,
        player_id=PLAYER_ID,
        player_is_active=True,
    )


@pytest.fixture
def active_session() -> SessionContext:
    return SessionContext(
        club_id=CLUB_ID,
        status=SessionStatus.ACTIVE,
        statistician_player_id=OTHER_PLAYER_ID,
    )


def test_player_can_start_only_when_no_session_exists(player: ActorContext) -> None:
    assert can_start_session(player, CLUB_ID, has_active_session=False)
    assert not can_start_session(player, CLUB_ID, has_active_session=True)
    assert not can_start_session(player, OTHER_CLUB_ID, has_active_session=False)
    assert not can_start_session(
        replace(player, player_is_active=False), CLUB_ID, has_active_session=False
    )
    assert not can_start_session(replace(player, player_id=None), CLUB_ID, has_active_session=False)


def test_player_cannot_manage_or_edit(player: ActorContext, active_session: SessionContext) -> None:
    assert not can_manage_current_session(player, active_session)
    assert not can_create_game(player, active_session)
    assert not can_edit_game(player, active_session)
    assert not can_manage_players(player, CLUB_ID)
    assert not can_manage_club(player, CLUB_ID)


def test_statistician_can_manage_only_active_current_session(
    player: ActorContext, active_session: SessionContext
) -> None:
    statistician = replace(player, player_id=OTHER_PLAYER_ID)
    assert can_manage_current_session(statistician, active_session)
    assert can_create_game(statistician, active_session)
    assert can_edit_game(statistician, active_session)
    assert not can_manage_players(statistician, CLUB_ID)
    assert not can_manage_club(statistician, CLUB_ID)

    for status in (SessionStatus.FINISHED, SessionStatus.VOID):
        past = replace(active_session, status=status)
        assert not can_manage_current_session(statistician, past)
        assert not can_create_game(statistician, past)
        assert not can_edit_game(statistician, past)
    assert not can_manage_current_session(
        statistician, replace(active_session, club_id=OTHER_CLUB_ID)
    )
    assert not can_create_game(replace(statistician, player_is_active=False), active_session)


def test_admin_can_manage_club_and_historical_games(
    player: ActorContext, active_session: SessionContext
) -> None:
    admin = replace(player, membership_role=MembershipRole.ADMIN)
    assert can_start_session(admin, CLUB_ID, has_active_session=False)
    assert can_manage_current_session(admin, active_session)
    assert can_create_game(admin, active_session)
    assert can_edit_game(admin, replace(active_session, status=SessionStatus.FINISHED))
    assert can_edit_game(admin, replace(active_session, status=SessionStatus.VOID))
    assert can_manage_players(admin, CLUB_ID)
    assert can_manage_club(admin, CLUB_ID)
    assert not can_manage_players(admin, OTHER_CLUB_ID)
    assert not can_manage_club(admin, OTHER_CLUB_ID)
    assert not can_manage_current_session(
        admin, replace(active_session, status=SessionStatus.FINISHED)
    )
    assert not can_edit_game(admin, replace(active_session, club_id=OTHER_CLUB_ID))


def test_admin_without_player_can_manage_club_but_not_start_session(
    player: ActorContext,
) -> None:
    admin = replace(player, membership_role=MembershipRole.ADMIN, player_id=None)
    assert can_manage_club(admin, CLUB_ID)
    assert can_manage_players(admin, CLUB_ID)
    assert not can_start_session(admin, CLUB_ID, has_active_session=False)


@pytest.mark.parametrize("membership_status", [MembershipStatus.BLOCKED, None])
def test_blocked_or_missing_membership_denies_access(
    player: ActorContext, active_session: SessionContext, membership_status: MembershipStatus | None
) -> None:
    actor = replace(
        player,
        membership_role=MembershipRole.ADMIN,
        membership_status=membership_status,
        player_id=OTHER_PLAYER_ID,
    )
    assert not can_start_session(actor, CLUB_ID, has_active_session=False)
    assert not can_manage_current_session(actor, active_session)
    assert not can_create_game(actor, active_session)
    assert not can_edit_game(actor, active_session)
    assert not can_manage_players(actor, CLUB_ID)
    assert not can_manage_club(actor, CLUB_ID)


def test_missing_membership_role_denies_access(player: ActorContext) -> None:
    actor = replace(player, membership_role=None)
    assert not can_start_session(actor, CLUB_ID, has_active_session=False)
    assert not can_manage_club(actor, CLUB_ID)
