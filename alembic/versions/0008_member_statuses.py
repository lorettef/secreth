"""Add inactive club-membership status.

Revision ID: 0008_member_statuses
Revises: 0007_player_profiles
"""

from alembic import op


revision = "0008_member_statuses"
down_revision = "0007_player_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL requires enum additions outside the surrounding transaction
    # before the new value can be used by the data migration.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE membership_status ADD VALUE IF NOT EXISTS 'INACTIVE'")
    op.execute(
        """
        UPDATE club_memberships AS membership
        SET status = 'INACTIVE'
        FROM players AS player
        WHERE membership.club_id = player.club_id
          AND membership.telegram_user_id = player.telegram_user_id
          AND membership.status = 'ACTIVE'
          AND player.is_active IS FALSE
        """
    )


def downgrade() -> None:
    # Restore the representation understood by 0007 code. Player.is_active is
    # intentionally left false, so legacy eligibility semantics are preserved.
    op.execute(
        "UPDATE club_memberships SET status = 'ACTIVE' WHERE status = 'INACTIVE'"
    )
    # PostgreSQL cannot remove an enum value additively. Leaving the now-unused
    # label is safe and makes a subsequent upgrade idempotent.
