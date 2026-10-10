"""Analysis-owned, commit-free batched cancellation lifecycle adapters.

Only active metadata changes. finished_at is the existing terminal timestamp;
there is no cancelled_at column, and no schema change is needed.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.source_analysis import OrmSourceAnalysis
from app.models.run_aggregation import OrmRunAggregation
from app.modules.analysis.domain.source_analysis import SourceAnalysisStatus
from app.modules.analysis.domain.run_aggregation import RunAggregationStatus


class CancelSourceAnalysesAdapter:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def cancel_for_run(self, run_id: UUID, now: datetime) -> int:
        rows = await self._session.scalars(
            update(OrmSourceAnalysis)
            .where(OrmSourceAnalysis.run_id == run_id,
                   OrmSourceAnalysis.status.in_([SourceAnalysisStatus.PENDING, SourceAnalysisStatus.RUNNING]))
            .values(status=SourceAnalysisStatus.CANCELLED, claim_token=None,
                    lease_expires_at=None, finished_at=now, updated_at=now)
            .returning(OrmSourceAnalysis.id)
        )
        return len(rows.all())


class CancelRunAggregationsAdapter:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def cancel_for_run(self, run_id: UUID, now: datetime) -> int:
        rows = await self._session.scalars(
            update(OrmRunAggregation)
            .where(OrmRunAggregation.run_id == run_id,
                   OrmRunAggregation.status.in_([RunAggregationStatus.PENDING.value, RunAggregationStatus.RUNNING.value]))
            .values(status=RunAggregationStatus.CANCELLED.value, claim_token=None,
                    lease_expires_at=None, finished_at=now, updated_at=now)
            .returning(OrmRunAggregation.id)
        )
        return len(rows.all())
