"""SQLAlchemy persistence adapter for prepared crawled documents."""

from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CrawledDocument
from app.modules.documents.domain import PreparedDocumentRecord


class SqlAlchemyDocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def replace_for_source(
        self, job_id: UUID, source_id: UUID, documents: list[PreparedDocumentRecord]
    ) -> int:
        inserted = 0
        for document in documents:
            if await self._exists(job_id, str(document.url)):
                continue
            self._session.add(
                CrawledDocument(
                    job_id=job_id,
                    source_id=source_id,
                    url=str(document.url),
                    canonical_url=document.canonical_url,
                    title=document.title,
                    published_at=document.published_at,
                    published_at_source=document.published_at_source,
                    published_at_confidence=document.published_at_confidence,
                    published_at_raw=document.published_at_raw,
                    raw_html=None,
                    clean_text=document.clean_text,
                    markdown=document.markdown,
                    language=document.language,
                    matched_score=document.matched_score,
                    content_hash=document.content_hash,
                )
            )
            inserted += 1
        await self._session.flush()
        return inserted

    async def source_counts(self, job_id: UUID) -> dict[UUID, int]:
        rows = (
            await self._session.execute(
                select(CrawledDocument.source_id, func.count(CrawledDocument.id))
                .where(CrawledDocument.job_id == job_id)
                .group_by(CrawledDocument.source_id)
            )
        ).all()
        return {source_id: int(count) for source_id, count in rows}

    async def clear_for_job(self, job_id: UUID) -> None:
        await self._session.execute(delete(CrawledDocument).where(CrawledDocument.job_id == job_id))

    async def _exists(self, job_id: UUID, url: str) -> bool:
        return bool(
            await self._session.scalar(
                select(CrawledDocument.id).where(
                    CrawledDocument.job_id == job_id, CrawledDocument.url == url
                )
            )
        )
