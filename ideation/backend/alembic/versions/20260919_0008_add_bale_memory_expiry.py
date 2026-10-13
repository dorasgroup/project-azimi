"""add Bale conversation memory expiry

Revision ID: 20260919_0008
Revises: 20260915_0007
Create Date: 2026-09-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260919_0008"
down_revision: str | None = "20260915_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "bale_conversations",
        sa.Column("memory_expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bale_conversations", "memory_expires_at")
