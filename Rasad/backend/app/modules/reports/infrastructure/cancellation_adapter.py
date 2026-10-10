"""Reporting-owned cancellation in the shared parent-first transaction."""

from datetime import datetime
from uuid import UUID
from sqlalchemy import update
from app.models.report_generation import OrmReportGeneration


class CancelReportGenerationsAdapter:
    def __init__(self, session) -> None:
        self._session = session

    async def cancel_for_run(self, run_id: UUID, now: datetime) -> int:
        rows = await self._session.scalars(update(OrmReportGeneration).where(
            OrmReportGeneration.run_id == run_id,
            OrmReportGeneration.status.in_(["pending", "running"]),
        ).values(status="cancelled", claim_token=None, lease_expires_at=None,
                 cancelled_at=now, updated_at=now).returning(OrmReportGeneration.id))
        return len(rows.all())
