from uuid import UUID

import pytest

from app.domain.enums import EventType, RuinReason
from app.domain.events import EventValidationError, validate_event

PLAYER = UUID(int=1)


@pytest.mark.parametrize("reason", list(RuinReason))
@pytest.mark.parametrize("admitted", [False, True])
def test_ruin_reasons_and_admitted(reason, admitted):
    fields = validate_event(PLAYER, [PLAYER], EventType.RUIN, reason, admitted)
    assert fields.ruin_reason is reason and fields.self_admitted is admitted


@pytest.mark.parametrize("event_type", [EventType.PALIVO, EventType.IQ_200, EventType.PROPHET])
def test_non_ruin_clears_ruin_fields(event_type):
    fields = validate_event(PLAYER, [PLAYER], event_type, RuinReason.OTHER, True)
    assert fields.ruin_reason is None and fields.self_admitted is False


@pytest.mark.parametrize("comment,expected", [(None, None), ("", None), (" \n ", None),
                                              (" a ", "a"), ("я" * 500, "я" * 500)])
def test_comment_normalization(comment, expected):
    assert validate_event(PLAYER, [PLAYER], EventType.PROPHET, comment=comment).comment == expected


@pytest.mark.parametrize("fields", [
    {"event_type": EventType.RUIN},
    {"event_type": EventType.RUIN, "ruin_reason": "UNKNOWN"},
    {"event_type": "CLOWN"},
    {"event_type": EventType.PALIVO, "self_admitted": "yes"},
    {"event_type": EventType.PALIVO, "comment": "x" * 501},
    {"event_type": EventType.PALIVO, "comment": 123},
])
def test_invalid_event_fields_rejected(fields):
    with pytest.raises(EventValidationError):
        validate_event(PLAYER, [PLAYER], **fields)


def test_non_participant_rejected():
    with pytest.raises(EventValidationError):
        validate_event(UUID(int=2), [PLAYER], EventType.PROPHET)


def test_exactly_four_types_and_seven_reasons():
    assert {t.value for t in EventType} == {"RUIN", "PALIVO", "IQ_200", "PROPHET"}
    assert len(RuinReason) == 7
