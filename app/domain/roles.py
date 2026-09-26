"""Role distribution and game role validation."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Collection, Iterable, Sequence
from uuid import UUID

from app.domain.enums import GameRole


class RoleValidationError(ValueError):
    """A game has an invalid participant or role selection."""


@dataclass(frozen=True)
class RoleDistribution:
    liberals: int
    fascists: int
    hitler: int = 1


ROLE_DISTRIBUTION = MappingProxyType({
    5: RoleDistribution(3, 1),
    6: RoleDistribution(4, 1),
    7: RoleDistribution(4, 2),
    8: RoleDistribution(5, 2),
    9: RoleDistribution(5, 3),
    10: RoleDistribution(6, 3),
})


def validate_player_count(player_count: int) -> None:
    if type(player_count) is not int or player_count not in ROLE_DISTRIBUTION:
        raise RoleValidationError("A game must have 5–10 players")


def get_role_distribution(player_count: int) -> RoleDistribution:
    validate_player_count(player_count)
    return ROLE_DISTRIBUTION[player_count]


def _validate_participants(participants: Sequence[UUID]) -> set[UUID]:
    validate_player_count(len(participants))
    participant_ids = set(participants)
    if len(participant_ids) != len(participants):
        raise RoleValidationError("Participants must be unique")
    return participant_ids


def assign_liberals(
    participants: Sequence[UUID], hitler: UUID, fascists: Iterable[UUID]
) -> tuple[UUID, ...]:
    """Return Liberal IDs in participant order after checking the selected roles."""
    participant_ids = _validate_participants(participants)
    fascist_ids = tuple(fascists)
    if hitler not in participant_ids:
        raise RoleValidationError("Hitler must be a participant")
    if len(set(fascist_ids)) != len(fascist_ids):
        raise RoleValidationError("Fascists must be unique")
    if hitler in fascist_ids:
        raise RoleValidationError("Hitler cannot also be a Fascist")
    if not set(fascist_ids) <= participant_ids:
        raise RoleValidationError("Every Fascist must be a participant")
    distribution = get_role_distribution(len(participants))
    if len(fascist_ids) != distribution.fascists:
        raise RoleValidationError(f"Expected {distribution.fascists} Fascists")
    excluded = set(fascist_ids) | {hitler}
    return tuple(player_id for player_id in participants if player_id not in excluded)


def validate_game_roles(
    participants: Sequence[UUID],
    assignments: Iterable[tuple[UUID, GameRole]],
    *,
    club_player_ids: Collection[UUID],
) -> None:
    """Validate one role row per participant against the club's player IDs."""
    participant_ids = _validate_participants(participants)
    if not participant_ids <= set(club_player_ids):
        raise RoleValidationError("Every participant must belong to the club")
    role_rows = tuple(assignments)
    assigned_ids = [player_id for player_id, _ in role_rows]
    if len(set(assigned_ids)) != len(assigned_ids):
        raise RoleValidationError("A participant cannot have multiple roles")
    if set(assigned_ids) != participant_ids:
        raise RoleValidationError("Roles must cover exactly the game participants")
    if any(not isinstance(role, GameRole) for _, role in role_rows):
        raise RoleValidationError("Unknown game role")
    distribution = get_role_distribution(len(participants))
    counts = {role: sum(row_role is role for _, row_role in role_rows) for role in GameRole}
    if counts[GameRole.HITLER] != distribution.hitler:
        raise RoleValidationError("Exactly one Hitler is required")
    if counts[GameRole.FASCIST] != distribution.fascists:
        raise RoleValidationError(f"Expected {distribution.fascists} Fascists")
    if counts[GameRole.LIBERAL] != distribution.liberals:
        raise RoleValidationError(f"Expected {distribution.liberals} Liberals")
