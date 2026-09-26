"""Winner and victory type consistency rules."""

from app.domain.enums import Team, VictoryType


class VictoryValidationError(ValueError):
    """The winning team and victory type are inconsistent."""


VICTORY_WINNERS = {
    VictoryType.LIBERAL_POLICIES: Team.LIBERAL,
    VictoryType.HITLER_KILLED: Team.LIBERAL,
    VictoryType.FASCIST_POLICIES: Team.FASCIST,
    VictoryType.HITLER_CHANCELLOR: Team.FASCIST,
}


def winner_from_victory_type(
    victory_type: VictoryType, *, allow_unknown: bool = False
) -> Team | None:
    if not isinstance(victory_type, VictoryType):
        raise VictoryValidationError("Unknown victory type")
    if victory_type is VictoryType.UNKNOWN:
        if allow_unknown:
            return None
        raise VictoryValidationError("UNKNOWN is only valid for legacy imports")
    return VICTORY_WINNERS[victory_type]


def validate_victory(
    winner: Team, victory_type: VictoryType, *, allow_unknown: bool = False
) -> None:
    if not isinstance(winner, Team):
        raise VictoryValidationError("Unknown winning team")
    expected_winner = winner_from_victory_type(victory_type, allow_unknown=allow_unknown)
    if expected_winner is not None and winner is not expected_winner:
        raise VictoryValidationError("Victory type does not match the winning team")
