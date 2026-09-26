"""Stable domain codes shared by persistence and business rules."""

import enum


class MembershipRole(str, enum.Enum):
    PLAYER = "PLAYER"
    ADMIN = "ADMIN"


class MembershipStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    BLOCKED = "BLOCKED"


class SessionStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    FINISHED = "FINISHED"
    VOID = "VOID"


class GameStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    VOID = "VOID"


class Team(str, enum.Enum):
    LIBERAL = "LIBERAL"
    FASCIST = "FASCIST"


class VictoryType(str, enum.Enum):
    LIBERAL_POLICIES = "LIBERAL_POLICIES"
    HITLER_KILLED = "HITLER_KILLED"
    FASCIST_POLICIES = "FASCIST_POLICIES"
    HITLER_CHANCELLOR = "HITLER_CHANCELLOR"
    UNKNOWN = "UNKNOWN"


class GameRole(str, enum.Enum):
    LIBERAL = "LIBERAL"
    FASCIST = "FASCIST"
    HITLER = "HITLER"


class EventType(str, enum.Enum):
    RUIN = "RUIN"
    PALIVO = "PALIVO"
    IQ_200 = "IQ_200"
    PROPHET = "PROPHET"


class RuinReason(str, enum.Enum):
    WRONG_VOTE = "WRONG_VOTE"
    ROLE_REVEAL = "ROLE_REVEAL"
    BAD_CHANCELLOR = "BAD_CHANCELLOR"
    BAD_EXECUTION = "BAD_EXECUTION"
    LEAKED_INFO = "LEAKED_INFO"
    TACTICAL_ERROR = "TACTICAL_ERROR"
    OTHER = "OTHER"
