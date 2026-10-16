"""Add registered club-member profile fields.

Revision ID: 0007_player_profiles
Revises: 0006_bootstrap_invites
"""

from alembic import op
import sqlalchemy as sa


revision = "0007_player_profiles"
down_revision = "0006_bootstrap_invites"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("players", sa.Column("first_name", sa.String(255), nullable=True))
    op.add_column("players", sa.Column("last_name", sa.String(255), nullable=True))
    op.add_column(
        "players", sa.Column("school_nickname", sa.String(255), nullable=True)
    )
    op.create_index(
        "uq_players_club_school_nickname_ci",
        "players",
        ["club_id", sa.text("lower(school_nickname)")],
        unique=True,
        postgresql_where=sa.text("school_nickname IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_players_club_school_nickname_ci",
        table_name="players",
    )
    op.drop_column("players", "school_nickname")
    op.drop_column("players", "last_name")
    op.drop_column("players", "first_name")
