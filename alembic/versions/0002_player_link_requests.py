"""Add administrator-reviewed historical player links.

Revision ID: 0002_player_link_requests
Revises: 0001_initial_models
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_player_link_requests"
down_revision = "0001_initial_models"
branch_labels = None
depends_on = None

player_link_status = postgresql.ENUM(
    "PENDING", "APPROVED", "REJECTED", name="player_link_status", create_type=False
)


def upgrade() -> None:
    player_link_status.create(op.get_bind(), checkfirst=False)
    op.create_table(
        "player_link_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("club_id", sa.Uuid(), sa.ForeignKey("clubs.id"), nullable=False),
        sa.Column("telegram_user_id", sa.Uuid(), sa.ForeignKey("telegram_users.id"), nullable=False),
        sa.Column("player_id", sa.Uuid(), sa.ForeignKey("players.id"), nullable=False),
        sa.Column("status", player_link_status, server_default="PENDING", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_by_player_id", sa.Uuid(), sa.ForeignKey("players.id")),
    )
    op.create_index(
        "uq_player_link_requests_pending_user_club",
        "player_link_requests",
        ["club_id", "telegram_user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    op.create_index(
        "uq_player_link_requests_pending_player",
        "player_link_requests",
        ["player_id"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    op.create_index(
        "ix_player_link_requests_club_status",
        "player_link_requests",
        ["club_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_player_link_requests_club_status", table_name="player_link_requests")
    op.drop_index("uq_player_link_requests_pending_player", table_name="player_link_requests")
    op.drop_index("uq_player_link_requests_pending_user_club", table_name="player_link_requests")
    op.drop_table("player_link_requests")
    player_link_status.drop(op.get_bind(), checkfirst=False)
