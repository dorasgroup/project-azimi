"""add Bale progress message id

Revision ID: 20260919_0009
Revises: 20260919_0008
Create Date: 2026-09-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260919_0009"
down_revision: str | None = "20260919_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "bale_updates",
        sa.Column("progress_message_id", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bale_updates", "progress_message_id")
