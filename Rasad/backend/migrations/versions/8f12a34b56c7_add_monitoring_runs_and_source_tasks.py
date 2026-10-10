"""add monitoring runs and source tasks

Revision ID: 8f12a34b56c7
Revises: f15a2d74c901
Create Date: 2026-09-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "8f12a34b56c7"
down_revision: str | None = "f15a2d74c901"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUN_STATUSES = ("pending", "running", "completed", "failed", "cancelled")
TASK_STATUSES = ("pending", "running", "completed", "failed", "cancelled")


def upgrade() -> None:
    # 1. Create Enums
    run_status = postgresql.ENUM(*RUN_STATUSES, name="monitoring_run_status")
    run_status.create(op.get_bind(), checkfirst=True)

    task_status = postgresql.ENUM(*TASK_STATUSES, name="source_task_status")
    task_status.create(op.get_bind(), checkfirst=True)

    # 2. Create monitoring_runs table
    op.create_table(
        "monitoring_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "monitoring_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("monitoring_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(*RUN_STATUSES, name="monitoring_run_status", create_type=False),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("expected_sources", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("terminal_sources", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("successful_sources", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_sources", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_monitoring_runs_job_id",
        "monitoring_runs",
        ["monitoring_job_id"],
    )
    op.create_index(
        "ix_monitoring_runs_status",
        "monitoring_runs",
        ["status"],
    )

    # 3. Create source_tasks table
    op.create_table(
        "source_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("monitoring_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sources.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(*TASK_STATUSES, name="source_task_status", create_type=False),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("documents_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("run_id", "source_id", name="uq_source_tasks_run_id_source_id"),
    )
    op.create_index(
        "ix_source_tasks_run_id",
        "source_tasks",
        ["run_id"],
    )
    op.create_index(
        "ix_source_tasks_run_id_status",
        "source_tasks",
        ["run_id", "status"],
    )


def downgrade() -> None:
    op.drop_table("source_tasks")
    op.drop_table("monitoring_runs")
    postgresql.ENUM(name="source_task_status").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="monitoring_run_status").drop(op.get_bind(), checkfirst=True)
