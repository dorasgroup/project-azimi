"""SQLAlchemy repository for source analysis results and document snapshots."""

from __future__ import annotations

import logging
from typing import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.source_analysis import OrmSourceAnalysis, OrmSourceAnalysisResult
from app.models.source_task_document import SourceTaskDocument
from app.modules.analysis.domain.source_analysis import (
    SourceAnalysisResultData,
    SourceAnalysisStatus,
)
from app.modules.analysis.ports.source_analysis import SourceTaskDocumentRecord

logger = logging.getLogger("rasad.analysis.result_repository")


class SqlAlchemySourceAnalysisResultRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save_result_fenced(
        self,
        analysis_id: UUID,
        claim_token: UUID,
        result_data: SourceAnalysisResultData,
    ) -> bool:
        """Persist analysis result transactionally protected by claim token fence."""
        stmt = (
            select(OrmSourceAnalysis)
            .where(
                OrmSourceAnalysis.id == analysis_id,
                OrmSourceAnalysis.status == SourceAnalysisStatus.RUNNING,
                OrmSourceAnalysis.claim_token == claim_token,
            )
            .with_for_update()
        )
        analysis_row = (await self._session.execute(stmt)).scalar_one_or_none()
        if analysis_row is None:
            logger.warning(
                "Stale worker rejected by fence for analysis %s (token %s). Discarding result.",
                analysis_id,
                claim_token,
            )
            return False

        # Idempotently insert result
        insert_stmt = (
            insert(OrmSourceAnalysisResult)
            .values(
                source_analysis_id=analysis_row.id,
                source_task_id=analysis_row.source_task_id,
                run_id=analysis_row.run_id,
                source_id=analysis_row.source_id,
                schema_version=result_data.schema_version,
                input_digest=result_data.input_digest,
                summary=result_data.summary,
                findings=result_data.findings,
                evidence_map=result_data.evidence_map,
                raw_result=result_data.raw_result,
            )
            .on_conflict_do_nothing(index_elements=["source_analysis_id"])
        )
        await self._session.execute(insert_stmt)
        return True

    async def get_by_analysis_id(self, analysis_id: UUID) -> SourceAnalysisResultData | None:
        stmt = select(OrmSourceAnalysisResult).where(
            OrmSourceAnalysisResult.source_analysis_id == analysis_id
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        return SourceAnalysisResultData(
            schema_version=row.schema_version,
            input_digest=row.input_digest,
            summary=row.summary,
            findings=row.findings,
            evidence_map=row.evidence_map,
            raw_result=row.raw_result,
        )


class SqlAlchemySourceTaskDocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_documents_for_task(
        self, source_task_id: UUID
    ) -> Sequence[SourceTaskDocumentRecord]:
        stmt = (
            select(SourceTaskDocument)
            .where(SourceTaskDocument.source_task_id == source_task_id)
            .order_by(SourceTaskDocument.url.asc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return rows
