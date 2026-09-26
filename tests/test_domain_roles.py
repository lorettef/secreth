"""Pure role distribution and validation tests."""

from uuid import UUID

import pytest

from app.domain.enums import GameRole
from app.domain.roles import (
    RoleDistribution,
    RoleValidationError,
    assign_liberals,
    get_role_distribution,
    validate_game_roles,
    validate_player_count,
)


def player_ids(count: int) -> tuple[UUID, ...]:
    return tuple(UUID(int=index + 1) for index in range(count))


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (5, RoleDistribution(3, 1)),
        (6, RoleDistribution(4, 1)),
        (7, RoleDistribution(4, 2)),
        (8, RoleDistribution(5, 2)),
        (9, RoleDistribution(5, 3)),
        (10, RoleDistribution(6, 3)),
    ],
)
def test_distribution_and_derived_liberals(count: int, expected: RoleDistribution) -> None:
    participants = player_ids(count)
    distribution = get_role_distribution(count)
    assert distribution == expected
    assert distribution.liberals + distribution.fascists + distribution.hitler == count
    validate_player_count(count)

    fascists = participants[1 : 1 + distribution.fascists]
    liberals = assign_liberals(participants, participants[0], fascists)
    assert liberals == participants[1 + distribution.fascists :]
    validate_game_roles(
        participants,
        [(participants[0], GameRole.HITLER)]
        + [(player, GameRole.FASCIST) for player in fascists]
        + [(player, GameRole.LIBERAL) for player in liberals],
        club_player_ids=participants,
    )


@pytest.mark.parametrize("count", [4, 11, 0, True])
def test_invalid_player_count(count: int) -> None:
    with pytest.raises(RoleValidationError):
        validate_player_count(count)
    with pytest.raises(RoleValidationError):
        get_role_distribution(count)


def test_duplicate_participants_and_unknown_selections() -> None:
    participants = player_ids(8)
    outsider = UUID(int=99)
    with pytest.raises(RoleValidationError, match="unique"):
        assign_liberals(participants[:-1] + (participants[0],), participants[0], participants[1:3])
    with pytest.raises(RoleValidationError, match="Hitler must"):
        assign_liberals(participants, outsider, participants[1:3])
    with pytest.raises(RoleValidationError, match="Every Fascist"):
        assign_liberals(participants, participants[0], (participants[1], outsider))


@pytest.mark.parametrize("count", [4, 11])
def test_game_role_entry_points_reject_invalid_game_sizes(count: int) -> None:
    participants = player_ids(count)
    with pytest.raises(RoleValidationError, match="5–10"):
        assign_liberals(participants, participants[0], participants[1:2])
    with pytest.raises(RoleValidationError, match="5–10"):
        validate_game_roles(participants, [], club_player_ids=participants)


@pytest.mark.parametrize("fascist_indexes", [(1,), (1, 2, 3)])
def test_wrong_fascist_count(fascist_indexes: tuple[int, ...]) -> None:
    participants = player_ids(8)
    with pytest.raises(RoleValidationError, match="Expected 2 Fascists"):
        assign_liberals(participants, participants[0], (participants[i] for i in fascist_indexes))


def test_hitler_overlap_and_duplicate_fascists() -> None:
    participants = player_ids(8)
    with pytest.raises(RoleValidationError, match="Hitler cannot"):
        assign_liberals(participants, participants[0], (participants[0], participants[1]))
    with pytest.raises(RoleValidationError, match="unique"):
        assign_liberals(participants, participants[0], (participants[1], participants[1]))


def valid_rows(participants: tuple[UUID, ...]) -> list[tuple[UUID, GameRole]]:
    return (
        [(participants[0], GameRole.HITLER)]
        + [(player, GameRole.FASCIST) for player in participants[1:3]]
        + [(player, GameRole.LIBERAL) for player in participants[3:]]
    )


def test_role_rows_require_exactly_one_hitler() -> None:
    participants = player_ids(8)
    rows = valid_rows(participants)
    rows[0] = (participants[0], GameRole.LIBERAL)
    with pytest.raises(RoleValidationError, match="Exactly one Hitler"):
        validate_game_roles(participants, rows, club_player_ids=participants)

    rows = valid_rows(participants)
    rows[3] = (participants[3], GameRole.HITLER)
    with pytest.raises(RoleValidationError, match="Exactly one Hitler"):
        validate_game_roles(participants, rows, club_player_ids=participants)


def test_hitler_cannot_have_a_second_fascist_role_row() -> None:
    participants = player_ids(8)
    rows = valid_rows(participants)
    rows[1] = (participants[0], GameRole.FASCIST)
    with pytest.raises(RoleValidationError, match="multiple roles"):
        validate_game_roles(participants, rows, club_player_ids=participants)


@pytest.mark.parametrize("fascist_count", [1, 3])
def test_role_rows_require_exact_fascist_count(fascist_count: int) -> None:
    participants = player_ids(8)
    rows = [(participants[0], GameRole.HITLER)]
    rows += [(player, GameRole.FASCIST) for player in participants[1 : 1 + fascist_count]]
    rows += [(player, GameRole.LIBERAL) for player in participants[1 + fascist_count :]]
    with pytest.raises(RoleValidationError, match="Expected 2 Fascists"):
        validate_game_roles(participants, rows, club_player_ids=participants)


def test_role_rows_reject_duplicates_missing_and_unknown_players() -> None:
    participants = player_ids(8)
    rows = valid_rows(participants)
    with pytest.raises(RoleValidationError, match="multiple roles"):
        validate_game_roles(participants, rows[:-1] + [rows[0]], club_player_ids=participants)
    with pytest.raises(RoleValidationError, match="cover exactly"):
        validate_game_roles(participants, rows[:-1], club_player_ids=participants)
    with pytest.raises(RoleValidationError, match="cover exactly"):
        validate_game_roles(
            participants,
            rows[:-1] + [(UUID(int=99), GameRole.LIBERAL)],
            club_player_ids=participants,
        )
    with pytest.raises(RoleValidationError, match="Unknown game role"):
        validate_game_roles(
            participants,
            rows[:-1] + [(participants[-1], "LIBERAL")],
            club_player_ids=participants,
        )


def test_role_rows_reject_participant_outside_club() -> None:
    participants = player_ids(8)
    with pytest.raises(RoleValidationError, match="belong to the club"):
        validate_game_roles(
            participants,
            valid_rows(participants),
            club_player_ids=participants[:-1],
        )
