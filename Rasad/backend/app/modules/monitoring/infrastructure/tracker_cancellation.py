"""Commit-free tracker closure; never creates a missing tracker or invents counts."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.completion_tracker import OrmRunCompletionTracker
from app.modules.monitoring.domain.completion_tracker import RunCompletionTrackerStatus


class CancelCompletionTrackerAdapter:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def cancel_for_run(self, run_id: UUID, now: datetime) -> int:
        # ANALYSIS_READY closes the control workflow, not its completed result.
        # CONTROL_FAILED is historical terminal failure and stays unchanged.
        rows = await self._session.scalars(
            update(OrmRunCompletionTracker)
            .where(OrmRunCompletionTracker.run_id == run_id,
                   OrmRunCompletionTracker.status.in_([
                       RunCompletionTrackerStatus.OPEN.value,
                       RunCompletionTrackerStatus.READY_ALL_TERMINAL.value,
                       RunCompletionTrackerStatus.READY_DEADLINE.value,
                       RunCompletionTrackerStatus.AGGREGATION_DISPATCHED.value,
                       RunCompletionTrackerStatus.ANALYSIS_READY.value,
                   ]))
            .values(status=RunCompletionTrackerStatus.CANCELLED.value, updated_at=now)
            .returning(OrmRunCompletionTracker.id)
        )
        return len(rows.all())
