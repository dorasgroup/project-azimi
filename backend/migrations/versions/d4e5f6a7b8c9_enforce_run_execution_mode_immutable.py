"""enforce run execution mode immutable

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-09-26
"""

from collections.abc import Sequence
from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION trg_prevent_monitoring_run_execution_mode_update()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.execution_mode IS DISTINCT FROM OLD.execution_mode THEN
                RAISE EXCEPTION 'execution_mode of MonitoringRun is immutable once created';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("DROP TRIGGER IF EXISTS trg_monitoring_runs_immutable_execution_mode ON monitoring_runs;")
    op.execute("""
        CREATE TRIGGER trg_monitoring_runs_immutable_execution_mode
        BEFORE UPDATE ON monitoring_runs
        FOR EACH ROW
        EXECUTE FUNCTION trg_prevent_monitoring_run_execution_mode_update();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_monitoring_runs_immutable_execution_mode ON monitoring_runs;")
    op.execute("DROP FUNCTION IF EXISTS trg_prevent_monitoring_run_execution_mode_update();")
