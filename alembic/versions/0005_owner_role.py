"""Add the unique club owner membership role.

Revision ID: 0005_owner_role
Revises: 0004_statistician_recovery
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_owner_role"
down_revision = "0004_statistician_recovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL must commit a new enum value before it can be referenced by the
    # partial-index predicate created below.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE membership_role ADD VALUE IF NOT EXISTS 'OWNER'")

    op.create_index(
        "uq_club_memberships_one_owner_per_club",
        "club_memberships",
        ["club_id"],
        unique=True,
        postgresql_where=sa.text("role = 'OWNER'"),
    )


def downgrade() -> None:
    owner_count = op.get_bind().scalar(
        sa.text("SELECT count(*) FROM club_memberships WHERE role = 'OWNER'")
    )
    if owner_count:
        raise RuntimeError("Cannot downgrade while OWNER memberships exist")

    op.drop_index(
        "uq_club_memberships_one_owner_per_club",
        table_name="club_memberships",
    )
    # PostgreSQL cannot remove one enum value without replacing the type and
    # rewriting dependent columns. Keep the unused value on a safe downgrade.
