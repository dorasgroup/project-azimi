"""Exercise article ingestion against Alembic, not only create_all metadata."""

import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.integrations.haminyar.service import ingest_content
from app.integrations.haminyar.source import ArticleContent, ArticleMetadata
from app.models import Category, SiteArticle, Upload, User


@pytest.mark.asyncio
async def test_article_pipeline_on_deployed_migration_constraints(monkeypatch, tmp_path):
    url = os.getenv("TEST_DATABASE_URL")
    if not url or "articles_test" not in url:
        pytest.skip("isolated migrated articles_test database required")
    engine = create_async_engine(url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(settings, "async_ingestion_enabled", True)
    monkeypatch.setattr(settings, "upload_storage_path", tmp_path)
    monkeypatch.setattr("app.services.upload_service.publish_ingestion_job", AsyncMock())
    async with maker() as session:
        constraint = await session.scalar(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid='uploads'::regclass AND conname='ck_uploads_source'"
            )
        )
        assert "haminyar_article" in constraint
        owner = User(
            email=f"migrated-{uuid4()}@example.com",
            full_name="Migration test",
            hashed_password="disabled",
            role="super_admin",
            is_active=True,
        )
        category = (
            await session.scalars(select(Category).where(Category.name == "ازدواج"))
        ).first()
        if category is None:
            category = Category(name="ازدواج")
            session.add(category)
        metadata = ArticleMetadata(
            2147483646,
            "مقاله آزمون ساختار واقعی",
            "https://haminyar.ir/marriage-guides/test",
            datetime.now(UTC),
            datetime.now(UTC),
        )
        article = SiteArticle(
            external_id=metadata.external_id,
            title=metadata.title,
            source_url=metadata.source_url,
            published_at=metadata.published_at,
            modified_at=metadata.modified_at,
            last_seen_at=datetime.now(UTC),
            source_status="published",
            revision=0,
        )
        session.add_all([owner, article])
        await session.commit()
        assert await ingest_content(
            session,
            article,
            ArticleContent(metadata, "متن مقاله آزمایشی برای پایگاه‌داده مهاجرت‌یافته"),
            owner_id=owner.id,
            category_id=category.id,
        )
        upload = await session.get(Upload, article.pending_upload_id)
        assert upload.source == "haminyar_article" and upload.status == "queued"
    await engine.dispose()
