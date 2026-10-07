"""Common parent-first DB lock for lifecycle writers; no domain dependency.

The parent lock is held until the caller's commit/rollback. Child identity reads
are non-locking; IDs/run membership are immutable. Never lock a child first.
"""

from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement


async def lock_run(session: AsyncSession, run_id: UUID) -> str | None:
    return await session.scalar(text(
        "SELECT status FROM monitoring_runs WHERE id = :run_id FOR UPDATE"
    ), {"run_id": run_id})


async def lock_parent_for_child(
    session: AsyncSession,
    run_column: ColumnElement[UUID],
    id_column: ColumnElement[UUID],
    child_id: UUID,
) -> str | None:
    run_id = await session.scalar(select(run_column).where(id_column == child_id))
    return await lock_run(session, run_id) if run_id is not None else None
