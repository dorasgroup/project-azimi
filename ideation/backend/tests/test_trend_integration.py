import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.integration_deps import require_trend_integration
from app.core.config import settings
from app.models import Category, IngestionJob, TrendImport, Upload, User
from app.models.upload import upload_categories
from app.repositories.upload_repository import UploadRepository
from app.schemas.trend_integration import TrendContentItem, TrendImportRequest
from app.services.trend_category_service import choose_trend_category
from app.services.trend_import_service import (
    TrendImportConflict,
    canonical_content_hash,
    list_trend_imports,
    receive_trend,
    render_trend_txt,
    trend_status,
)

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


def _payload(**updates) -> TrendImportRequest:
    data = {
        "source_system": "trend-monitor",
        "source_trend_id": "cluster-42",
        "source_version": 1,
        "title": "ترند آزمایشی",
        "topic": "فناوری",
        "score": 87.5,
        "detected_at": datetime(2026, 9, 14, 6, 0, tzinfo=UTC),
        "status": "trending",
        "summary": "خلاصه‌ای پایدار برای آزمون دریافت ترند.",
        "items": [
            TrendContentItem(
                external_id="post-1",
                source_name="منبع نمونه",
                source_url="https://example.com/post-1",
                published_at=datetime(2026, 9, 14, 5, 30, tzinfo=UTC),
                text="متن محتوای مرتبط",
            )
        ],
    }
    data.update(updates)
    return TrendImportRequest(**data)


def test_rendered_txt_and_hash_are_deterministic() -> None:
    payload = _payload()
    assert canonical_content_hash(payload) == canonical_content_hash(payload)
    rendered = render_trend_txt(payload)
    assert rendered == render_trend_txt(payload)
    assert "ترند آزمایشی".encode() in rendered
    assert rendered.endswith(b"\n")


def test_marriage_trend_is_matched_from_topic_and_text() -> None:
    marriage = Category(id=uuid4(), name="ازدواج")
    women = Category(id=uuid4(), name="کنشگری زنان")
    production = Category(id=uuid4(), name="گروه تولید")
    payload = _payload(
        title="پاسخ فرمانده به زوج جوان",
        topic="اخبار اجتماعی",
        summary="زوج جوان حلقه ازدواجشان را به رزمندگان هدیه کردند.",
        items=[
            TrendContentItem(
                external_id="marriage-post",
                source_name="خبرگزاری نمونه",
                source_url="https://example.com/marriage",
                text="این زوج جوان در مراسمی از حلقه ازدواج و زندگی مشترک گفتند.",
            )
        ],
    )

    assert choose_trend_category([women, production, marriage], payload) is marriage


def test_ambiguous_or_unrelated_trend_is_left_uncategorized() -> None:
    categories = [
        Category(id=uuid4(), name="ازدواج"),
        Category(id=uuid4(), name="کنشگری زنان"),
    ]
    payload = _payload(
        title="گزارش وضعیت آب و هوا",
        topic="هواشناسی",
        summary="بارش باران در مناطق شمالی ادامه دارد.",
        items=[],
    )

    assert choose_trend_category(categories, payload) is None


def test_single_generic_production_word_does_not_force_a_category() -> None:
    production = Category(id=uuid4(), name="گروه تولید")
    payload = _payload(
        title="تحلیل یک سخنرانی سیاسی",
        topic="سیاست",
        summary="سخنران گفت طرف مقابل برای تولید روایت رسانه‌ای تلاش می‌کند.",
        items=[],
    )

    assert choose_trend_category([production], payload) is None


@pytest.mark.asyncio
async def test_integration_is_hidden_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr(settings, "trend_integration_enabled", False)
    with pytest.raises(HTTPException) as exc:
        await require_trend_integration("irrelevant")
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_integration_rejects_missing_configuration_and_invalid_token(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "trend_integration_enabled", True)
    monkeypatch.setattr(settings, "trend_integration_token", None)
    monkeypatch.setattr(settings, "trend_integration_user_id", None)
    with pytest.raises(HTTPException) as missing:
        await require_trend_integration("anything")
    assert missing.value.status_code == 503

    monkeypatch.setattr(settings, "trend_integration_token", SecretStr("expected-token"))
    monkeypatch.setattr(settings, "trend_integration_user_id", uuid4())
    with pytest.raises(HTTPException) as invalid:
        await require_trend_integration("wrong-token")
    assert invalid.value.status_code == 401

    principal = await require_trend_integration("expected-token")
    assert principal.user_id == settings.trend_integration_user_id


@pytest_asyncio.fixture
async def database():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is required")
    engine = create_async_engine(TEST_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_receive_is_idempotent_and_exposes_queue_status(
    database,
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "async_ingestion_enabled", True)
    monkeypatch.setattr(settings, "upload_storage_path", tmp_path)
    publish = AsyncMock()
    monkeypatch.setattr("app.services.upload_service.publish_ingestion_job", publish)
    user_id = uuid4()
    trend_id = f"cluster-{uuid4()}"
    idempotency_key = f"trend-monitor:{trend_id}:v1"
    async with database() as session:
        session.add(
            User(
                id=user_id,
                email=f"trend-integration-{uuid4()}@example.com",
                full_name="Trend integration",
                hashed_password="not-a-production-credential",
                role="user",
                is_active=True,
            )
        )
        category = Category(id=uuid4(), name=f"ازدواج-{uuid4()}")
        session.add(category)
        await session.commit()
        payload = _payload(
            source_trend_id=trend_id,
            topic=category.name,
            summary="گزارش مرتبط با موضوع دسته انتخاب‌شده.",
        )
        first = await receive_trend(
            session,
            payload=payload,
            idempotency_key=idempotency_key,
            user_id=user_id,
            request_id="phase1-test",
        )
        duplicate = await receive_trend(
            session,
            payload=payload,
            idempotency_key=idempotency_key,
            user_id=user_id,
            request_id="phase1-test-repeat",
        )

        assert first.upload_id == duplicate.upload_id
        assert duplicate.duplicate is True
        assert first.delivery_status == "stored"
        assert first.dify_status == "queued"
        assert first.title == payload.title
        assert first.topic == payload.topic
        assert first.category_names == [category.name]
        assert duplicate.category_names == [category.name]
        assert first.score == payload.score
        assert publish.await_count == 1
        assert (
            await session.scalar(
                select(func.count())
                .select_from(upload_categories)
                .where(
                    upload_categories.c.upload_id == first.upload_id,
                    upload_categories.c.category_id == category.id,
                )
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count()).select_from(TrendImport).where(
                    TrendImport.source_trend_id == trend_id
                )
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count()).select_from(Upload).where(
                    Upload.uploaded_by == user_id
                )
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(IngestionJob)
                .join(Upload)
                .where(Upload.uploaded_by == user_id)
            )
            == 1
        )

        status_result = await trend_status(
            session,
            source_system="trend-monitor",
            source_trend_id=trend_id,
            source_version=1,
        )
        assert status_result is not None
        assert status_result.upload_id == first.upload_id
        assert status_result.ingestion_job_id is not None

        listed = await list_trend_imports(session)
        assert any(item.source_trend_id == trend_id for item in listed.items)

        visible_recent = await UploadRepository(session).list_all(
            uploaded_by=user_id,
            exclude_trend_imports=True,
        )
        assert visible_recent == []

        changed = _payload(
            source_trend_id=trend_id,
            summary="محتوای متفاوت با همان کلید",
        )
        with pytest.raises(TrendImportConflict):
            await receive_trend(
                session,
                payload=changed,
                idempotency_key=idempotency_key,
                user_id=user_id,
                request_id="phase1-test-conflict",
            )
