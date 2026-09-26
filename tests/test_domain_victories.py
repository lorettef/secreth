"""Pure winner and victory type rules."""

import pytest

from app.domain.enums import Team, VictoryType
from app.domain.victories import (
    VictoryValidationError,
    validate_victory,
    winner_from_victory_type,
)


@pytest.mark.parametrize(
    ("winner", "victory_type"),
    [
        (Team.LIBERAL, VictoryType.LIBERAL_POLICIES),
        (Team.LIBERAL, VictoryType.HITLER_KILLED),
        (Team.FASCIST, VictoryType.FASCIST_POLICIES),
        (Team.FASCIST, VictoryType.HITLER_CHANCELLOR),
    ],
)
def test_valid_victories(winner: Team, victory_type: VictoryType) -> None:
    assert winner_from_victory_type(victory_type) is winner
    validate_victory(winner, victory_type)


@pytest.mark.parametrize(
    ("winner", "victory_type"),
    [
        (Team.LIBERAL, VictoryType.FASCIST_POLICIES),
        (Team.LIBERAL, VictoryType.HITLER_CHANCELLOR),
        (Team.FASCIST, VictoryType.LIBERAL_POLICIES),
        (Team.FASCIST, VictoryType.HITLER_KILLED),
    ],
)
def test_mismatched_victories(winner: Team, victory_type: VictoryType) -> None:
    with pytest.raises(VictoryValidationError, match="does not match"):
        validate_victory(winner, victory_type)


def test_unknown_requires_explicit_legacy_permission() -> None:
    with pytest.raises(VictoryValidationError, match="legacy"):
        winner_from_victory_type(VictoryType.UNKNOWN)
    with pytest.raises(VictoryValidationError, match="legacy"):
        validate_victory(Team.LIBERAL, VictoryType.UNKNOWN)
    assert winner_from_victory_type(VictoryType.UNKNOWN, allow_unknown=True) is None
    validate_victory(Team.LIBERAL, VictoryType.UNKNOWN, allow_unknown=True)
    validate_victory(Team.FASCIST, VictoryType.UNKNOWN, allow_unknown=True)


@pytest.mark.parametrize("victory_type", [None, "LIBERAL_POLICIES", "INVALID"])
def test_invalid_victory_type(victory_type: object) -> None:
    with pytest.raises(VictoryValidationError, match="Unknown victory type"):
        winner_from_victory_type(victory_type)


@pytest.mark.parametrize("winner", [None, "LIBERAL", "UNKNOWN"])
def test_invalid_winner(winner: object) -> None:
    with pytest.raises(VictoryValidationError, match="Unknown winning team"):
        validate_victory(winner, VictoryType.LIBERAL_POLICIES)
