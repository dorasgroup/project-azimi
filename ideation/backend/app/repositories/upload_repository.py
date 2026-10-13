from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.trend_import import TrendImport
from app.models.upload import Upload


class UploadRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_hash(self, sha256_hash: str, uploaded_by: UUID) -> Upload | None:
        result = await self.session.execute(
            select(Upload)
            .where(
                Upload.sha256_hash == sha256_hash,
                Upload.uploaded_by == uploaded_by,
                Upload.deleted_at.is_(None),
            )
            .options(selectinload(Upload.categories))
        )
        return result.scalar_one_or_none()

    async def get_by_source_key(self, source_key: str) -> Upload | None:
        result = await self.session.execute(
            select(Upload)
            .where(Upload.source_key == source_key, Upload.deleted_at.is_(None))
            .options(selectinload(Upload.categories))
        )
        return result.scalar_one_or_none()

    async def create(self, upload: Upload) -> Upload:
        self.session.add(upload)
        await self.session.flush()
        return upload

    async def get_by_id(self, upload_id: UUID, uploaded_by: UUID | None = None) -> Upload | None:
        conditions = [Upload.id == upload_id, Upload.deleted_at.is_(None)]
        if uploaded_by is not None:
            conditions.append(Upload.uploaded_by == uploaded_by)
        result = await self.session.execute(
            select(Upload)
            .where(*conditions)
            .options(selectinload(Upload.categories))
        )
        return result.scalar_one_or_none()

    async def list_all(
        self,
        uploaded_by: UUID | None = None,
        limit: int = 100,
        exclude_trend_imports: bool = False,
    ) -> list[Upload]:
        conditions = [Upload.deleted_at.is_(None)]
        if uploaded_by is not None:
            conditions.append(Upload.uploaded_by == uploaded_by)
        if exclude_trend_imports:
            # Public site articles have their own paginated receipt/indexing section.
            conditions.append(Upload.source != "haminyar_article")
            conditions.append(
                ~exists(
                    select(TrendImport.id).where(TrendImport.upload_id == Upload.id)
                )
            )
        result = await self.session.execute(
            select(Upload)
            .where(*conditions)
            .options(selectinload(Upload.categories))
            .order_by(Upload.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def soft_delete(self, upload: Upload) -> None:
        upload.deleted_at = datetime.now(UTC)
        upload.status = "deleted"
        await self.session.flush()
