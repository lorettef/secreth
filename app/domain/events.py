"""Event fields independent of Telegram and persistence."""

from dataclasses import dataclass
from uuid import UUID
from typing import Collection

from app.domain.enums import EventType, RuinReason

COMMENT_LIMIT = 500


class EventValidationError(ValueError):
    pass


@dataclass(frozen=True)
class EventFields:
    event_type: EventType
    ruin_reason: RuinReason | None
    self_admitted: bool
    comment: str | None


def normalize_comment(comment: str | None) -> str | None:
    if comment is not None and (not isinstance(comment, str) or len(comment) > COMMENT_LIMIT):
        raise EventValidationError("Invalid comment")
    if comment is None:
        return None
    return comment.strip() or None


def validate_event(player_id: UUID, participants: Collection[UUID], event_type: EventType,
                   ruin_reason: RuinReason | None = None, self_admitted: bool = False,
                   comment: str | None = None) -> EventFields:
    if player_id not in participants:
        raise EventValidationError("Player is not a game participant")
    if not isinstance(event_type, EventType) or type(self_admitted) is not bool:
        raise EventValidationError("Invalid event fields")
    if event_type is EventType.RUIN:
        if not isinstance(ruin_reason, RuinReason):
            raise EventValidationError("RUIN requires a reason")
    else:
        # Editing RUIN into another type must clear its fields as well.
        ruin_reason, self_admitted = None, False
    return EventFields(event_type, ruin_reason, self_admitted, normalize_comment(comment))
