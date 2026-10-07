"""add execution leases and active run index

Revision ID: a1b2c3d4e5f6
Revises: 8f12a34b56c7
Create Date: 2026-09-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "8f12a34b56c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add cancelled_sources to monitoring_runs
    op.add_column(
        "monitoring_runs",
        sa.Column("cancelled_sources", sa.Integer(), nullable=False, server_default="0"),
    )

    # 2. Add partial unique index for active run per job
    op.create_index(
        "uq_monitoring_runs_active_job",
        "monitoring_runs",
        ["monitoring_job_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )

    # 3. Add lease and retry columns to source_tasks
    op.add_column(
        "source_tasks",
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "source_tasks",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "source_tasks",
        sa.Column("claim_token", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "source_tasks",
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )

    # 4. Add index for finding expired leases
    op.create_index(
        "ix_source_tasks_status_lease_expires",
        "source_tasks",
        ["status", "lease_expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_source_tasks_status_lease_expires", table_name="source_tasks")
    op.drop_column("source_tasks", "attempt_count")
    op.drop_column("source_tasks", "claim_token")
    op.drop_column("source_tasks", "lease_expires_at")
    op.drop_column("source_tasks", "claimed_at")
    op.drop_index("uq_monitoring_runs_active_job", table_name="monitoring_runs")
    op.drop_column("monitoring_runs", "cancelled_sources")
