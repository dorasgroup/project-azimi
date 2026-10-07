"""Alembic autogeneration parity for the Phase 7/7G physical schema."""

from __future__ import annotations

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.base import Base
from app.models import *  # noqa: F403 - load all mapped tables as migration env does


TARGET_TABLES = {
    "run_completion_trackers",
    "run_completion_events",
    "run_aggregations",
    "run_aggregation_inputs",
    "aggregated_analysis_results",
    "integration_outbox",
}


def _table_name(change: tuple) -> str | None:
    action = change[0]
    if action in {"add_table", "remove_table"}:
        return change[1].name
    if action in {"add_column", "remove_column", "modify_type",
                  "modify_nullable", "modify_default", "modify_comment"}:
        return change[2]
    if action in {"add_index", "remove_index", "add_constraint", "remove_constraint"}:
        return change[1].table.name
    return None


@pytest.mark.asyncio
async def test_phase7h_outbox_and_phase7_schema_parity() -> None:
    assert TARGET_TABLES.issubset(Base.metadata.tables)

    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            changes = await connection.run_sync(
                lambda sync_conn: compare_metadata(
                    MigrationContext.configure(sync_conn, opts={"compare_type": True}),
                    Base.metadata,
                )
            )
    finally:
        await engine.dispose()

    targeted: list[tuple] = []
    for change in changes:
        for operation in change if isinstance(change, list) else [change]:
            if _table_name(operation) in TARGET_TABLES:
                targeted.append(operation)
    assert targeted == [], f"Phase 7/Outbox ORM vs migrated PostgreSQL drift: {targeted}"
