"""SQLAlchemy persistence adapter for source configuration."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Source
from app.shared.domain.errors import ConflictError
from app.modules.crawling.domain.source_catalogue import is_operational_source_url


class SqlAlchemySourceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list(self, page: int, page_size: int, *, operational_only: bool = False):
        filters = []
        if operational_only:
            # Filter BEFORE pagination/counting so fixtures cannot displace real
            # sources. Parse hosts rather than guessing from user-facing titles.
            candidates = (await self._session.execute(select(Source.id, Source.base_url))).all()
            ids = [sid for sid, url in candidates if is_operational_source_url(url)]
            filters.append(Source.id.in_(ids))
        total = int(await self._session.scalar(select(func.count()).select_from(Source).where(*filters)) or 0)
        query = (
            select(Source)
            .where(*filters)
            .order_by(Source.created_at.desc(), Source.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = list((await self._session.scalars(query)).all())
        return rows, total

    async def get(self, source_id: UUID):
        return await self._session.get(Source, source_id)

    async def add(self, source: Source) -> None:
        self._session.add(source)
        await self._flush_with_conflict_translation()

    async def delete(self, source: Source) -> None:
        await self._session.delete(source)

    async def flush(self) -> None:
        await self._flush_with_conflict_translation()

    async def _flush_with_conflict_translation(self) -> None:
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise ConflictError("Source base URL already exists") from exc
