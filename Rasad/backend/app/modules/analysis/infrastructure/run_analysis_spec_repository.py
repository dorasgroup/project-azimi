"""SQLAlchemy implementation of RunAnalysisSpecRepositoryPort."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import logging
from typing import Any, Mapping
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.source_analysis import OrmRunAnalysisSpec
from app.modules.analysis.domain.run_analysis_spec import RunAnalysisSpec
from app.modules.analysis.ports.run_analysis_spec import RunAnalysisSpecRepositoryPort

logger = logging.getLogger("rasad.analysis.run_spec_repository")


class SqlAlchemyRunAnalysisSpecRepository(RunAnalysisSpecRepositoryPort):
    """PostgreSQL repository for managing durable RunAnalysisSpec snapshots."""

    def __init__(self, session: AsyncSession | async_sessionmaker[AsyncSession]) -> None:
        self._session = session

    @asynccontextmanager
    async def _get_session(self) -> AsyncIterator[AsyncSession]:
        if isinstance(self._session, async_sessionmaker):
            async with self._session() as session:
                yield session
                await session.commit()
        else:
            yield self._session

    def _to_domain(self, row: OrmRunAnalysisSpec) -> RunAnalysisSpec:
        return RunAnalysisSpec(
            id=row.id,
            run_id=row.run_id,
            spec=dict(row.spec),
            spec_digest=row.spec_digest,
            created_at=row.created_at,
        )

    async def get_by_run_id(self, run_id: UUID) -> RunAnalysisSpec | None:
        """Fetch the RunAnalysisSpec for a monitoring run, if it exists."""
        stmt = select(OrmRunAnalysisSpec).where(OrmRunAnalysisSpec.run_id == run_id)
        async with self._get_session() as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return self._to_domain(row) if row is not None else None

    async def ensure(
        self,
        run_id: UUID,
        spec: Mapping[str, Any],
        spec_digest: str,
    ) -> RunAnalysisSpec:
        """Atomically and idempotently insert or retrieve the RunAnalysisSpec."""
        async with self._get_session() as session:
            stmt = (
                insert(OrmRunAnalysisSpec)
                .values(
                    run_id=run_id,
                    spec=dict(spec),
                    spec_digest=spec_digest,
                )
                .on_conflict_do_nothing(index_elements=["run_id"])
                .returning(OrmRunAnalysisSpec)
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                # Already created by a concurrent transaction; retrieve the existing snapshot
                stmt_select = select(OrmRunAnalysisSpec).where(OrmRunAnalysisSpec.run_id == run_id)
                row = (await session.execute(stmt_select)).scalar_one()

            await session.flush()
            return self._to_domain(row)
