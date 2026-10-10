"""add publication date provenance

Revision ID: c7e31f4a92b0
Revises: a6cabff23785
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "c7e31f4a92b0"
down_revision: str | None = "a6cabff23785"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "crawled_documents",
        sa.Column("published_at_source", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "crawled_documents",
        sa.Column("published_at_confidence", sa.Float(), nullable=True),
    )
    op.add_column(
        "crawled_documents",
        sa.Column("published_at_raw", sa.String(length=255), nullable=True),
    )
    op.create_check_constraint(
        "published_at_confidence_range",
        "crawled_documents",
        "published_at_confidence IS NULL OR "
        "(published_at_confidence >= 0 AND published_at_confidence <= 1)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "published_at_confidence_range",
        "crawled_documents",
        type_="check",
    )
    op.drop_column("crawled_documents", "published_at_raw")
    op.drop_column("crawled_documents", "published_at_confidence")
    op.drop_column("crawled_documents", "published_at_source")
