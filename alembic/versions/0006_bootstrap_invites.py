"""Allow one system bootstrap invitation per club.

Revision ID: 0006_bootstrap_invites
Revises: 0005_owner_role
"""

from alembic import op
import sqlalchemy as sa


revision = "0006_bootstrap_invites"
down_revision = "0005_owner_role"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "invite_tokens", "created_by", existing_type=sa.Uuid(), nullable=True
    )
    op.add_column(
        "invite_tokens",
        sa.Column(
            "is_bootstrap",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_invite_tokens_creator_origin",
        "invite_tokens",
        "(is_bootstrap AND created_by IS NULL) OR "
        "(NOT is_bootstrap AND created_by IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_invite_tokens_bootstrap_options",
        "invite_tokens",
        "NOT is_bootstrap OR (max_uses = 1 AND expires_at IS NULL)",
    )
    op.create_index(
        "uq_invite_tokens_bootstrap_per_club",
        "invite_tokens",
        ["club_id"],
        unique=True,
        postgresql_where=sa.text("is_bootstrap"),
    )


def downgrade() -> None:
    bootstrap_count = op.get_bind().scalar(
        sa.text("SELECT count(*) FROM invite_tokens WHERE is_bootstrap")
    )
    if bootstrap_count:
        raise RuntimeError("Cannot downgrade while bootstrap invitations exist")

    op.drop_index(
        "uq_invite_tokens_bootstrap_per_club",
        table_name="invite_tokens",
    )
    op.drop_constraint(
        "ck_invite_tokens_bootstrap_options",
        "invite_tokens",
        type_="check",
    )
    op.drop_constraint(
        "ck_invite_tokens_creator_origin",
        "invite_tokens",
        type_="check",
    )
    op.drop_column("invite_tokens", "is_bootstrap")
    op.alter_column(
        "invite_tokens", "created_by", existing_type=sa.Uuid(), nullable=False
    )
