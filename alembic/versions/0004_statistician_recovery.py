"""Persist emergency statistician recovery requests and confirmations.

Revision ID: 0004_statistician_recovery
Revises: 0003_game_draft_id
"""

from alembic import op
import sqlalchemy as sa

revision = "0004_statistician_recovery"
down_revision = "0003_game_draft_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "statistician_recoveries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("session_id", sa.Uuid(), sa.ForeignKey("game_sessions.id"), nullable=False),
        sa.Column("initiator_telegram_user_id", sa.Uuid(), sa.ForeignKey("telegram_users.id"), nullable=False),
        sa.Column("initiator_player_id", sa.Uuid(), sa.ForeignKey("players.id")),
        sa.Column("initiator_presence_id", sa.Uuid(), sa.ForeignKey("session_participants.id")),
        sa.Column("candidate_player_id", sa.Uuid(), sa.ForeignKey("players.id"), nullable=False),
        sa.Column("candidate_presence_id", sa.Uuid(), sa.ForeignKey("session_participants.id"), nullable=False),
        sa.Column("previous_statistician_player_id", sa.Uuid(), sa.ForeignKey("players.id"), nullable=False),
        sa.Column("status", sa.String(7), server_default="PENDING", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('PENDING', 'APPLIED', 'STALE')", name="ck_statistician_recoveries_status"),
    )
    op.create_index("ix_statistician_recoveries_session_status", "statistician_recoveries", ["session_id", "status"])
    op.create_table(
        "statistician_confirmations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("recovery_id", sa.Uuid(), sa.ForeignKey("statistician_recoveries.id"), nullable=False),
        sa.Column("telegram_user_id", sa.Uuid(), sa.ForeignKey("telegram_users.id"), nullable=False),
        sa.Column("player_id", sa.Uuid(), sa.ForeignKey("players.id"), nullable=False),
        sa.Column("presence_id", sa.Uuid(), sa.ForeignKey("session_participants.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("recovery_id", "telegram_user_id", name="uq_statistician_confirmation_user"),
        sa.UniqueConstraint("recovery_id", "player_id", name="uq_statistician_confirmation_player"),
    )


def downgrade() -> None:
    op.drop_table("statistician_confirmations")
    op.drop_index("ix_statistician_recoveries_session_status", table_name="statistician_recoveries")
    op.drop_table("statistician_recoveries")
