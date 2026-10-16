"""Persistent records for clubs, sessions, games, and audit history."""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.domain.enums import (
    EventType,
    GameRole,
    GameStatus,
    MembershipRole,
    MembershipStatus,
    PlayerLinkStatus,
    RecoveryStatus,
    RuinReason,
    SessionStatus,
    Team,
    VictoryType,
)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class TelegramUser(TimestampMixin, Base):
    __tablename__ = "telegram_users"
    __table_args__ = (
        UniqueConstraint("telegram_user_id", name="uq_telegram_users_telegram_user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    username: Mapped[str | None] = mapped_column(String(255))
    first_name: Mapped[str | None] = mapped_column(String(255))
    last_name: Mapped[str | None] = mapped_column(String(255))

    players: Mapped[list["Player"]] = relationship(back_populates="telegram_user")
    memberships: Mapped[list["ClubMembership"]] = relationship(back_populates="telegram_user")


class Club(TimestampMixin, Base):
    __tablename__ = "clubs"
    __table_args__ = (
        CheckConstraint("minimum_role_games_for_leaderboard >= 0", name="ck_clubs_min_role_games"),
        CheckConstraint("minimum_total_games_for_leaderboard >= 0", name="ck_clubs_min_total_games"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    timezone: Mapped[str] = mapped_column(String(100), nullable=False)
    minimum_role_games_for_leaderboard: Mapped[int] = mapped_column(
        Integer, server_default=text("5"), nullable=False
    )
    minimum_total_games_for_leaderboard: Mapped[int] = mapped_column(
        Integer, server_default=text("10"), nullable=False
    )

    players: Mapped[list["Player"]] = relationship(back_populates="club")
    sessions: Mapped[list["GameSession"]] = relationship(back_populates="club")
    memberships: Mapped[list["ClubMembership"]] = relationship(back_populates="club")


class Player(TimestampMixin, Base):
    __tablename__ = "players"
    __table_args__ = (
        UniqueConstraint("club_id", "telegram_user_id", name="uq_players_club_telegram_user"),
        Index("ix_players_club_active", "club_id", "is_active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    club_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clubs.id"), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    telegram_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("telegram_users.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)

    club: Mapped[Club] = relationship(back_populates="players")
    telegram_user: Mapped[TelegramUser | None] = relationship(back_populates="players")


class ClubMembership(Base):
    __tablename__ = "club_memberships"
    __table_args__ = (
        UniqueConstraint("club_id", "telegram_user_id", name="uq_memberships_club_telegram_user"),
        Index(
            "uq_club_memberships_one_owner_per_club",
            "club_id",
            unique=True,
            postgresql_where=text("role = 'OWNER'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    club_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clubs.id"), nullable=False)
    telegram_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("telegram_users.id"), nullable=False)
    role: Mapped[MembershipRole] = mapped_column(
        Enum(MembershipRole, name="membership_role"), nullable=False
    )
    status: Mapped[MembershipStatus] = mapped_column(
        Enum(MembershipStatus, name="membership_status"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    club: Mapped[Club] = relationship(back_populates="memberships")
    telegram_user: Mapped[TelegramUser] = relationship(back_populates="memberships")


class PlayerLinkRequest(Base):
    __tablename__ = "player_link_requests"
    __table_args__ = (
        Index(
            "uq_player_link_requests_pending_user_club",
            "club_id",
            "telegram_user_id",
            unique=True,
            postgresql_where=text("status = 'PENDING'"),
        ),
        Index(
            "uq_player_link_requests_pending_player",
            "player_id",
            unique=True,
            postgresql_where=text("status = 'PENDING'"),
        ),
        Index("ix_player_link_requests_club_status", "club_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    club_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clubs.id"), nullable=False)
    telegram_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("telegram_users.id"), nullable=False)
    player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id"), nullable=False)
    status: Mapped[PlayerLinkStatus] = mapped_column(
        Enum(PlayerLinkStatus, name="player_link_status"),
        server_default="PENDING",
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by_player_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("players.id"))

    player: Mapped[Player] = relationship(foreign_keys=[player_id])
    telegram_user: Mapped[TelegramUser] = relationship()


class InviteToken(Base):
    __tablename__ = "invite_tokens"
    __table_args__ = (
        UniqueConstraint("token", name="uq_invite_tokens_token"),
        CheckConstraint("max_uses IS NULL OR max_uses > 0", name="ck_invite_tokens_max_uses"),
        CheckConstraint("uses_count >= 0", name="ck_invite_tokens_uses_count"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    club_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clubs.id"), nullable=False)
    token: Mapped[str] = mapped_column(String(255), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    max_uses: Mapped[int | None] = mapped_column(Integer)
    uses_count: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class GameSession(TimestampMixin, Base):
    __tablename__ = "game_sessions"
    __table_args__ = (
        Index(
            "uq_game_sessions_one_active_per_club",
            "club_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        Index("ix_game_sessions_club_started", "club_id", "started_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    club_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clubs.id"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[SessionStatus] = mapped_column(
        Enum(SessionStatus, name="session_status"), server_default="ACTIVE", nullable=False
    )
    created_by_player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id"), nullable=False)
    statistician_player_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("players.id"), nullable=False
    )

    club: Mapped[Club] = relationship(back_populates="sessions")
    created_by_player: Mapped[Player] = relationship(foreign_keys=[created_by_player_id])
    statistician_player: Mapped[Player] = relationship(foreign_keys=[statistician_player_id])
    participants: Mapped[list["SessionParticipant"]] = relationship(back_populates="session")
    games: Mapped[list["Game"]] = relationship(back_populates="session")


class SessionParticipant(Base):
    __tablename__ = "session_participants"
    __table_args__ = (
        Index(
            "uq_session_participants_present",
            "session_id",
            "player_id",
            unique=True,
            postgresql_where=text("is_present"),
        ),
        CheckConstraint(
            "(is_present AND left_at IS NULL) OR (NOT is_present AND left_at IS NOT NULL)",
            name="ck_session_participants_presence_time",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("game_sessions.id"), nullable=False)
    player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id"), nullable=False)
    is_present: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    session: Mapped[GameSession] = relationship(back_populates="participants")
    player: Mapped[Player] = relationship()


class StatisticianRecovery(Base):
    __tablename__ = "statistician_recoveries"
    __table_args__ = (Index("ix_statistician_recoveries_session_status", "session_id", "status"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("game_sessions.id"), nullable=False)
    initiator_telegram_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("telegram_users.id"), nullable=False)
    initiator_player_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("players.id"))
    initiator_presence_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("session_participants.id"))
    candidate_player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id"), nullable=False)
    candidate_presence_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("session_participants.id"), nullable=False)
    previous_statistician_player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id"), nullable=False)
    status: Mapped[RecoveryStatus] = mapped_column(
        Enum(RecoveryStatus, name="ck_statistician_recoveries_status", native_enum=False,
             create_constraint=True), server_default="PENDING", nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class StatisticianConfirmation(Base):
    __tablename__ = "statistician_confirmations"
    __table_args__ = (
        UniqueConstraint("recovery_id", "telegram_user_id", name="uq_statistician_confirmation_user"),
        UniqueConstraint("recovery_id", "player_id", name="uq_statistician_confirmation_player"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    recovery_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("statistician_recoveries.id"), nullable=False)
    telegram_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("telegram_users.id"), nullable=False)
    player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id"), nullable=False)
    presence_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("session_participants.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Game(TimestampMixin, Base):
    __tablename__ = "games"
    __table_args__ = (
        UniqueConstraint("session_id", "sequence_number", name="uq_games_session_sequence"),
        UniqueConstraint("draft_id", name="uq_games_draft_id"),
        CheckConstraint("sequence_number > 0", name="ck_games_sequence_positive"),
        Index("ix_games_session_played", "session_id", "played_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    draft_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("game_sessions.id"), nullable=False)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    winner_team: Mapped[Team] = mapped_column(Enum(Team, name="team"), nullable=False)
    victory_type: Mapped[VictoryType | None] = mapped_column(Enum(VictoryType, name="victory_type"))
    status: Mapped[GameStatus] = mapped_column(
        Enum(GameStatus, name="game_status"), server_default="ACTIVE", nullable=False
    )
    played_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by_player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id"), nullable=False)

    session: Mapped[GameSession] = relationship(back_populates="games")
    players: Mapped[list["GamePlayer"]] = relationship(back_populates="game")
    events: Mapped[list["GameEvent"]] = relationship(back_populates="game")


class GamePlayer(Base):
    __tablename__ = "game_players"
    __table_args__ = (
        UniqueConstraint("game_id", "player_id", name="uq_game_players_game_player"),
        Index("ix_game_players_player", "player_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    game_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("games.id"), nullable=False)
    player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id"), nullable=False)
    role: Mapped[GameRole] = mapped_column(Enum(GameRole, name="game_role"), nullable=False)

    game: Mapped[Game] = relationship(back_populates="players")
    player: Mapped[Player] = relationship()


class GameEvent(TimestampMixin, Base):
    __tablename__ = "game_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["game_id", "player_id"],
            ["game_players.game_id", "game_players.player_id"],
            name="fk_game_events_game_participant",
        ),
        CheckConstraint(
            "event_type = 'RUIN' OR (ruin_reason IS NULL AND NOT self_admitted)",
            name="ck_game_events_ruin_only_fields",
        ),
        Index("ix_game_events_player_type", "player_id", "event_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    game_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("games.id"), nullable=False)
    player_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("players.id"), nullable=False)
    event_type: Mapped[EventType] = mapped_column(Enum(EventType, name="event_type"), nullable=False)
    ruin_reason: Mapped[RuinReason | None] = mapped_column(Enum(RuinReason, name="ruin_reason"))
    self_admitted: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    comment: Mapped[str | None] = mapped_column(String(500))
    created_by_player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id"), nullable=False)

    game: Mapped[Game] = relationship(back_populates="events")
    player: Mapped[Player] = relationship(foreign_keys=[player_id])


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_club_created", "club_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    club_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clubs.id"), nullable=False)
    actor_player_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("players.id"))
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    before_data: Mapped[dict | None] = mapped_column(JSONB)
    after_data: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
