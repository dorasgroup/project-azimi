"""SQLAlchemy report persistence and ownership queries."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import AnalysisReport


class SqlAlchemyReportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_accessible(
        self, page: int, page_size: int, user_id: UUID | None, role: str | None
    ) -> tuple[list[AnalysisReport], int]:
        filters = (
            []
            if role == "super_admin" or user_id is None
            else [AnalysisReport.created_by == user_id]
        )
        count = select(func.count()).select_from(AnalysisReport)
        query = (
            select(AnalysisReport)
            .order_by(AnalysisReport.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        if filters:
            count, query = count.where(*filters), query.where(*filters)
        total = int(await self._session.scalar(count) or 0)
        return list((await self._session.scalars(query)).all()), total

    async def get_accessible(
        self, report_id: UUID, user_id: UUID | None, role: str | None
    ) -> AnalysisReport | None:
        filters = [AnalysisReport.id == report_id]
        if role != "super_admin" and user_id is not None:
            filters.append(AnalysisReport.created_by == user_id)
        return await self._session.scalar(
            select(AnalysisReport).where(*filters).options(selectinload(AnalysisReport.job))
        )

    async def save(self, report: AnalysisReport) -> None:
        self._session.add(report)
        await self._session.flush()

    async def delete_for_job(self, job_id: UUID) -> None:
        from sqlalchemy import delete

        await self._session.execute(delete(AnalysisReport).where(AnalysisReport.job_id == job_id))

    async def flush(self) -> None:
        await self._session.flush()

    async def delete(self, report: AnalysisReport) -> None:
        if report.job is not None:
            await self._session.delete(report.job)
        else:
            await self._session.delete(report)
        await self._session.flush()
