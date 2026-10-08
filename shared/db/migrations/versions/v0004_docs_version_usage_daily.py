"""Documents version per tenant, daily usage, and an index for analytics (Phase 4).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-08 06:59:31.290734
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "usage_daily",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("questions", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("cache_hits", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("tokens_in", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("tokens_out", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "cost_usd",
            sa.Numeric(precision=14, scale=6),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_usage_daily_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "day", name=op.f("pk_usage_daily")),
    )
    op.create_index("ix_messages_tenant_id_created_at", "messages", ["tenant_id", "created_at"])
    op.add_column(
        "tenants",
        sa.Column("docs_version", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("tenants", "docs_version")
    op.drop_index("ix_messages_tenant_id_created_at", table_name="messages")
    op.drop_table("usage_daily")
