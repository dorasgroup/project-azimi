"""add trend import display metadata

Revision ID: 20260914_0004
Revises: 20260914_0003
Create Date: 2026-09-14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260914_0004"
down_revision: str | None = "20260914_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("trend_imports", sa.Column("title", sa.String(length=500), nullable=True))
    op.add_column("trend_imports", sa.Column("topic", sa.String(length=255), nullable=True))
    op.add_column(
        "trend_imports",
        sa.Column("trend_status", sa.String(length=50), nullable=True),
    )
    op.add_column("trend_imports", sa.Column("score", sa.Float(), nullable=True))
    op.add_column(
        "trend_imports",
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "trend_imports",
        sa.Column("dify_completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        sa.text(
            """
            UPDATE trend_imports
            SET title = 'ترند واردشده',
                trend_status = 'trending',
                detected_at = received_at
            WHERE title IS NULL OR trend_status IS NULL OR detected_at IS NULL
            """
        )
    )
    op.alter_column("trend_imports", "title", nullable=False)
    op.alter_column("trend_imports", "trend_status", nullable=False)
    op.alter_column("trend_imports", "detected_at", nullable=False)


def downgrade() -> None:
    op.drop_column("trend_imports", "dify_completed_at")
    op.drop_column("trend_imports", "detected_at")
    op.drop_column("trend_imports", "score")
    op.drop_column("trend_imports", "trend_status")
    op.drop_column("trend_imports", "topic")
    op.drop_column("trend_imports", "title")
