"""Statistics formulas and the six MVP leaderboard codes."""

from enum import StrEnum


class LeaderboardKind(StrEnum):
    OVERALL = "overall"
    LIBERAL = "liberal"
    FASCIST = "fascist"
    HITLER = "hitler"
    RUIN = "ruin"
    IQ_200 = "iq"


def percentage(numerator: int, denominator: int) -> float:
    return numerator / denominator * 100 if denominator else 0.0
