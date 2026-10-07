"""add deadline scheduling and result metadata
Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: str | None = "a7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "run_completion_trackers",
        sa.Column("deadline_scheduled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "aggregated_analysis_results",
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "aggregated_analysis_results",
        sa.Column("manifest_schema_version", sa.String(length=16), nullable=False, server_default="2.0"),
    )


def downgrade() -> None:
    op.drop_column("aggregated_analysis_results", "manifest_schema_version")
    op.drop_column("aggregated_analysis_results", "cutoff_at")
    op.drop_column("run_completion_trackers", "deadline_scheduled_at")
