"""add durable report generations and artifacts

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d0e1f2a3b4c5"
down_revision: str | None = "c9d0e1f2a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION prevent_aggregated_result_update() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'aggregated analysis result is immutable';
        END; $$
    """)
    op.execute("CREATE TRIGGER trg_aggregated_result_immutable BEFORE UPDATE ON aggregated_analysis_results FOR EACH ROW EXECUTE FUNCTION prevent_aggregated_result_update()")
    op.create_table(
        "report_generations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("monitoring_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("aggregated_analysis_result_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("aggregated_analysis_results.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("spec_json", postgresql.JSONB(), nullable=False),
        sa.Column("spec_digest", sa.String(64), nullable=False),
        sa.Column("manifest_digest", sa.String(64), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("claim_token", postgresql.UUID(as_uuid=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("failed_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(64)),
        sa.Column("sanitized_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("run_id", "aggregated_analysis_result_id", name="uq_report_generations_run_aggregate"),
        sa.CheckConstraint("status IN ('pending','running','completed','failed','cancelled')", name="valid_status"),
        sa.CheckConstraint("status <> 'running' OR (claim_token IS NOT NULL AND claimed_at IS NOT NULL AND lease_expires_at IS NOT NULL)", name="running_lease"),
        sa.CheckConstraint("attempt_count >= 0", name="nonnegative_attempts"),
    )
    op.create_index("ix_report_generations_run_id", "report_generations", ["run_id"])
    op.create_index("ix_report_generations_status_lease", "report_generations", ["status", "lease_expires_at"])
    op.execute("""
        CREATE FUNCTION prevent_report_generation_input_change() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.status IN ('completed','failed','cancelled') AND NEW IS DISTINCT FROM OLD THEN
                RAISE EXCEPTION 'report generation terminal state is immutable';
            END IF;
            IF NEW.run_id IS DISTINCT FROM OLD.run_id
               OR NEW.aggregated_analysis_result_id IS DISTINCT FROM OLD.aggregated_analysis_result_id
               OR NEW.spec_json IS DISTINCT FROM OLD.spec_json
               OR NEW.spec_digest IS DISTINCT FROM OLD.spec_digest
               OR NEW.manifest_digest IS DISTINCT FROM OLD.manifest_digest THEN
                RAISE EXCEPTION 'report generation input is immutable';
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER trg_report_generation_input_immutable
        BEFORE UPDATE ON report_generations
        FOR EACH ROW EXECUTE FUNCTION prevent_report_generation_input_change()
    """)
    op.create_table(
        "report_artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("report_generation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("report_generations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("format", sa.String(8), nullable=False),
        sa.Column("storage_key", sa.String(2048), nullable=False),
        sa.Column("content_checksum", sa.String(64), nullable=False),
        sa.Column("content_size", sa.Integer(), nullable=False),
        sa.Column("report_schema_version", sa.String(16), nullable=False),
        sa.Column("template_version", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("report_generation_id", "format", name="uq_report_artifacts_generation_format"),
        sa.CheckConstraint("format IN ('pdf','docx')", name="valid_format"),
        sa.CheckConstraint("content_size > 0", name="positive_size"),
        sa.CheckConstraint("content_checksum ~ '^[0-9a-f]{64}$'", name="valid_checksum"),
    )
    op.create_index("ix_report_artifacts_report_generation_id", "report_artifacts", ["report_generation_id"])


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_aggregated_result_immutable ON aggregated_analysis_results")
    op.execute("DROP FUNCTION IF EXISTS prevent_aggregated_result_update()")
    op.execute("DROP TRIGGER trg_report_generation_input_immutable ON report_generations")
    op.execute("DROP FUNCTION prevent_report_generation_input_change()")
    op.drop_index("ix_report_artifacts_report_generation_id", table_name="report_artifacts")
    op.drop_table("report_artifacts")
    op.drop_index("ix_report_generations_status_lease", table_name="report_generations")
    op.drop_index("ix_report_generations_run_id", table_name="report_generations")
    op.drop_table("report_generations")
