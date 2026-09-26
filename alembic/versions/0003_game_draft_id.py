"""Persist the save idempotency key on games.

Revision ID: 0003_game_draft_id
Revises: 0002_player_link_requests
"""

from alembic import op
import sqlalchemy as sa

revision = "0003_game_draft_id"
down_revision = "0002_player_link_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("games", sa.Column("draft_id", sa.Uuid(), nullable=True))
    op.create_unique_constraint("uq_games_draft_id", "games", ["draft_id"])


def downgrade() -> None:
    op.drop_constraint("uq_games_draft_id", "games", type_="unique")
    op.drop_column("games", "draft_id")
