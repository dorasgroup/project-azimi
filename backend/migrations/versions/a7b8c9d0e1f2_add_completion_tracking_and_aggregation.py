"""add completion tracking and aggregation
Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a7b8c9d0e1f2"
down_revision: str | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. run_completion_trackers
    op.create_table(
        "run_completion_trackers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("monitoring_runs.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="open"),
        sa.Column("analysis_terminal_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("analysis_successful_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("analysis_failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("analysis_cancelled_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("readiness_reason", sa.String(length=64), nullable=True),
        sa.Column("aggregation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("analysis_ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
    )
    op.create_index("ix_run_completion_trackers_run_id", "run_completion_trackers", ["run_id"], unique=True)
    op.create_index("ix_run_completion_trackers_status", "run_completion_trackers", ["status"])

    # 2. run_completion_events
    op.create_table(
        "run_completion_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("monitoring_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_kind", sa.String(length=32), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("event_kind", "entity_id", name="uq_run_completion_events_kind_entity"),
    )
    op.create_index("ix_run_completion_events_run_id", "run_completion_events", ["run_id"])

    # 3. run_aggregations
    op.create_table(
        "run_aggregations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("monitoring_runs.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("readiness_reason", sa.String(length=64), nullable=False),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("manifest_digest", sa.String(length=64), nullable=False),
        sa.Column("run_analysis_spec_digest", sa.String(length=64), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.create_index("ix_run_aggregations_run_id", "run_aggregations", ["run_id"], unique=True)
    op.create_index("ix_run_aggregations_status", "run_aggregations", ["status"])

    # 4. run_aggregation_inputs
    op.create_table(
        "run_aggregation_inputs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("aggregation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("run_aggregations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_analysis_result_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("source_analysis_results.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("input_digest", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False, server_default="1.0"),
        sa.UniqueConstraint("aggregation_id", "source_analysis_result_id", name="uq_run_aggregation_inputs_agg_result"),
    )
    op.create_index("ix_run_aggregation_inputs_aggregation_id", "run_aggregation_inputs", ["aggregation_id"])

    # 5. aggregated_analysis_results
    op.create_table(
        "aggregated_analysis_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("aggregation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("run_aggregations.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("monitoring_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False, server_default="1.0"),
        sa.Column("manifest_digest", sa.String(length=64), nullable=False),
        sa.Column("readiness_reason", sa.String(length=64), nullable=False),
        sa.Column("expected_sources", sa.Integer(), nullable=False),
        sa.Column("successful_sources", sa.Integer(), nullable=False),
        sa.Column("failed_sources", sa.Integer(), nullable=False),
        sa.Column("cancelled_sources", sa.Integer(), nullable=False),
        sa.Column("analysis_successful", sa.Integer(), nullable=False),
        sa.Column("analysis_failed", sa.Integer(), nullable=False),
        sa.Column("analysis_cancelled", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("findings", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("coverage_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("provenance_references", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("raw_result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_aggregated_analysis_results_aggregation_id", "aggregated_analysis_results", ["aggregation_id"], unique=True)
    op.create_index("ix_aggregated_analysis_results_run_id", "aggregated_analysis_results", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_aggregated_analysis_results_run_id", table_name="aggregated_analysis_results")
    op.drop_index("ix_aggregated_analysis_results_aggregation_id", table_name="aggregated_analysis_results")
    op.drop_table("aggregated_analysis_results")

    op.drop_index("ix_run_aggregation_inputs_aggregation_id", table_name="run_aggregation_inputs")
    op.drop_table("run_aggregation_inputs")

    op.drop_index("ix_run_aggregations_status", table_name="run_aggregations")
    op.drop_index("ix_run_aggregations_run_id", table_name="run_aggregations")
    op.drop_table("run_aggregations")

    op.drop_index("ix_run_completion_events_run_id", table_name="run_completion_events")
    op.drop_table("run_completion_events")

    op.drop_index("ix_run_completion_trackers_status", table_name="run_completion_trackers")
    op.drop_index("ix_run_completion_trackers_run_id", table_name="run_completion_trackers")
    op.drop_table("run_completion_trackers")
