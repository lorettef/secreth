"""Create the initial club, session, game, and audit schema.

Revision ID: 0001_initial_models
Revises:
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_initial_models"
down_revision = None
branch_labels = None
depends_on = None

membership_role = postgresql.ENUM("PLAYER", "ADMIN", name="membership_role", create_type=False)
membership_status = postgresql.ENUM("ACTIVE", "BLOCKED", name="membership_status", create_type=False)
session_status = postgresql.ENUM("ACTIVE", "FINISHED", "VOID", name="session_status", create_type=False)
game_status = postgresql.ENUM("ACTIVE", "VOID", name="game_status", create_type=False)
team = postgresql.ENUM("LIBERAL", "FASCIST", name="team", create_type=False)
victory_type = postgresql.ENUM(
    "LIBERAL_POLICIES", "HITLER_KILLED", "FASCIST_POLICIES", "HITLER_CHANCELLOR", "UNKNOWN",
    name="victory_type", create_type=False,
)
game_role = postgresql.ENUM("LIBERAL", "FASCIST", "HITLER", name="game_role", create_type=False)
event_type = postgresql.ENUM("RUIN", "PALIVO", "IQ_200", "PROPHET", name="event_type", create_type=False)
ruin_reason = postgresql.ENUM(
    "WRONG_VOTE", "ROLE_REVEAL", "BAD_CHANCELLOR", "BAD_EXECUTION", "LEAKED_INFO",
    "TACTICAL_ERROR", "OTHER", name="ruin_reason", create_type=False,
)
enum_types = (
    membership_role, membership_status, session_status, game_status, team,
    victory_type, game_role, event_type, ruin_reason,
)


def upgrade() -> None:
    for enum_type in enum_types:
        enum_type.create(op.get_bind(), checkfirst=False)

    op.create_table(
        "telegram_users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(255)),
        sa.Column("first_name", sa.String(255)),
        sa.Column("last_name", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("telegram_user_id", name="uq_telegram_users_telegram_user_id"),
    )
    op.create_table(
        "clubs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("timezone", sa.String(100), nullable=False),
        sa.Column("minimum_role_games_for_leaderboard", sa.Integer(), server_default="5", nullable=False),
        sa.Column("minimum_total_games_for_leaderboard", sa.Integer(), server_default="10", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("minimum_role_games_for_leaderboard >= 0", name="ck_clubs_min_role_games"),
        sa.CheckConstraint("minimum_total_games_for_leaderboard >= 0", name="ck_clubs_min_total_games"),
    )
    op.create_table(
        "players",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("club_id", sa.Uuid(), sa.ForeignKey("clubs.id"), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("telegram_user_id", sa.Uuid(), sa.ForeignKey("telegram_users.id")),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("club_id", "telegram_user_id", name="uq_players_club_telegram_user"),
    )
    op.create_index("ix_players_club_active", "players", ["club_id", "is_active"])
    op.create_table(
        "club_memberships",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("club_id", sa.Uuid(), sa.ForeignKey("clubs.id"), nullable=False),
        sa.Column("telegram_user_id", sa.Uuid(), sa.ForeignKey("telegram_users.id"), nullable=False),
        sa.Column("role", membership_role, nullable=False),
        sa.Column("status", membership_status, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("club_id", "telegram_user_id", name="uq_memberships_club_telegram_user"),
    )
    op.create_table(
        "invite_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("club_id", sa.Uuid(), sa.ForeignKey("clubs.id"), nullable=False),
        sa.Column("token", sa.String(255), nullable=False),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("players.id"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("max_uses", sa.Integer()),
        sa.Column("uses_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("token", name="uq_invite_tokens_token"),
        sa.CheckConstraint("max_uses IS NULL OR max_uses > 0", name="ck_invite_tokens_max_uses"),
        sa.CheckConstraint("uses_count >= 0", name="ck_invite_tokens_uses_count"),
    )
    op.create_table(
        "game_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("club_id", sa.Uuid(), sa.ForeignKey("clubs.id"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status", session_status, server_default="ACTIVE", nullable=False),
        sa.Column("created_by_player_id", sa.Uuid(), sa.ForeignKey("players.id"), nullable=False),
        sa.Column("statistician_player_id", sa.Uuid(), sa.ForeignKey("players.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_game_sessions_club_started", "game_sessions", ["club_id", "started_at"])
    op.create_index(
        "uq_game_sessions_one_active_per_club", "game_sessions", ["club_id"],
        unique=True, postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_table(
        "session_participants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("session_id", sa.Uuid(), sa.ForeignKey("game_sessions.id"), nullable=False),
        sa.Column("player_id", sa.Uuid(), sa.ForeignKey("players.id"), nullable=False),
        sa.Column("is_present", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("left_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "(is_present AND left_at IS NULL) OR (NOT is_present AND left_at IS NOT NULL)",
            name="ck_session_participants_presence_time",
        ),
    )
    op.create_index(
        "uq_session_participants_present", "session_participants", ["session_id", "player_id"],
        unique=True, postgresql_where=sa.text("is_present"),
    )
    op.create_table(
        "games",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("session_id", sa.Uuid(), sa.ForeignKey("game_sessions.id"), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("winner_team", team, nullable=False),
        sa.Column("victory_type", victory_type),
        sa.Column("status", game_status, server_default="ACTIVE", nullable=False),
        sa.Column("played_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by_player_id", sa.Uuid(), sa.ForeignKey("players.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("session_id", "sequence_number", name="uq_games_session_sequence"),
        sa.CheckConstraint("sequence_number > 0", name="ck_games_sequence_positive"),
    )
    op.create_index("ix_games_session_played", "games", ["session_id", "played_at"])
    op.create_table(
        "game_players",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("game_id", sa.Uuid(), sa.ForeignKey("games.id"), nullable=False),
        sa.Column("player_id", sa.Uuid(), sa.ForeignKey("players.id"), nullable=False),
        sa.Column("role", game_role, nullable=False),
        sa.UniqueConstraint("game_id", "player_id", name="uq_game_players_game_player"),
    )
    op.create_index("ix_game_players_player", "game_players", ["player_id"])
    op.create_table(
        "game_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("game_id", sa.Uuid(), sa.ForeignKey("games.id"), nullable=False),
        sa.Column("player_id", sa.Uuid(), sa.ForeignKey("players.id"), nullable=False),
        sa.Column("event_type", event_type, nullable=False),
        sa.Column("ruin_reason", ruin_reason),
        sa.Column("self_admitted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("comment", sa.String(500)),
        sa.Column("created_by_player_id", sa.Uuid(), sa.ForeignKey("players.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["game_id", "player_id"], ["game_players.game_id", "game_players.player_id"],
            name="fk_game_events_game_participant",
        ),
        sa.CheckConstraint(
            "event_type = 'RUIN' OR (ruin_reason IS NULL AND NOT self_admitted)",
            name="ck_game_events_ruin_only_fields",
        ),
    )
    op.create_index("ix_game_events_player_type", "game_events", ["player_id", "event_type"])
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("club_id", sa.Uuid(), sa.ForeignKey("clubs.id"), nullable=False),
        sa.Column("actor_player_id", sa.Uuid(), sa.ForeignKey("players.id")),
        sa.Column("entity_type", sa.String(100), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("before_data", postgresql.JSONB()),
        sa.Column("after_data", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_audit_logs_club_created", "audit_logs", ["club_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_logs_club_created", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index("ix_game_events_player_type", table_name="game_events")
    op.drop_table("game_events")
    op.drop_index("ix_game_players_player", table_name="game_players")
    op.drop_table("game_players")
    op.drop_index("ix_games_session_played", table_name="games")
    op.drop_table("games")
    op.drop_index("uq_session_participants_present", table_name="session_participants")
    op.drop_table("session_participants")
    op.drop_index("uq_game_sessions_one_active_per_club", table_name="game_sessions")
    op.drop_index("ix_game_sessions_club_started", table_name="game_sessions")
    op.drop_table("game_sessions")
    op.drop_table("invite_tokens")
    op.drop_table("club_memberships")
    op.drop_index("ix_players_club_active", table_name="players")
    op.drop_table("players")
    op.drop_table("clubs")
    op.drop_table("telegram_users")

    for enum_type in reversed(enum_types):
        enum_type.drop(op.get_bind(), checkfirst=False)
