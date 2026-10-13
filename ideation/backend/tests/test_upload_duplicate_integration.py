import os
from collections.abc import AsyncIterator
from hashlib import sha256
from io import BytesIO
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import UploadFile
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Category, Upload, User
from app.models.upload import upload_categories
from app.schemas.upload import UploadListItem
from app.services.upload_service import UploadService

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for the real AsyncSession integration tests",
)


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(TEST_DATABASE_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db_session:
        yield db_session
        await db_session.rollback()
    await engine.dispose()


async def _create_user(session: AsyncSession, suffix: str) -> User:
    user = User(
        email=f"phase1-{suffix}-{uuid4()}@example.com",
        full_name="Phase 1 integration test",
        hashed_password="not-a-real-credential",
        role="user",
        is_active=True,
    )
    session.add(user)
    await session.commit()
    return user


async def _create_categories(session: AsyncSession, count: int) -> list[Category]:
    categories = [Category(name=f"phase1-{uuid4()}") for _ in range(count)]
    session.add_all(categories)
    await session.commit()
    return categories


def _file(content: bytes = b"phase-one duplicate integration fixture") -> UploadFile:
    return UploadFile(filename="phase1-fixture.txt", file=BytesIO(content))


@pytest.mark.asyncio
@pytest.mark.parametrize("category_count", [0, 1, 3])
async def test_duplicate_upload_eager_loads_and_preserves_categories(
    session: AsyncSession,
    tmp_path,
    monkeypatch,
    category_count: int,
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "upload_storage_path", tmp_path)
    user = await _create_user(session, f"categories-{category_count}")
    categories = await _create_categories(session, category_count)
    service = UploadService(session)
    service.dify.create_document_by_file = AsyncMock(
        return_value={"document": {"id": str(uuid4())}}
    )

    first = await service.create_upload(
        user.id,
        _file(),
        [category.id for category in categories],
    )

    statements = 0

    def count_statement(*_args) -> None:
        nonlocal statements
        statements += 1

    event.listen(session.bind.sync_engine, "before_cursor_execute", count_statement)
    try:
        duplicate = await service.create_upload(
            user.id,
            _file(),
            [category.id for category in categories],
        )
    finally:
        event.remove(session.bind.sync_engine, "before_cursor_execute", count_statement)

    assert duplicate.model_dump() == {
        "id": first.id,
        "filename": "phase1-fixture.txt",
        "status": "indexed",
        "ingestion_job_id": None,
        "status_url": None,
        "request_id": None,
        "message": "This file already exists and was indexed in Dify",
    }
    assert service.dify.create_document_by_file.await_count == 1
    assert await session.scalar(
        select(func.count()).select_from(Upload).where(Upload.uploaded_by == user.id)
    ) == 1
    assert statements <= 8

    persisted = await service.uploads.get_by_hash(
        sha256(b"phase-one duplicate integration fixture").hexdigest(),
        user.id,
    )
    assert persisted is not None
    await session.refresh(persisted, ["categories"])
    assert {category.id for category in persisted.categories} == {
        category.id for category in categories
    }
    serialized = UploadListItem(
        id=str(persisted.id),
        original_filename=persisted.original_filename,
        file_size_bytes=persisted.file_size_bytes,
        mime_type=persisted.mime_type,
        status=persisted.status,
        categories=[
            {"id": category.id, "name": category.name}
            for category in persisted.categories
        ],
        created_at=persisted.created_at,
    )
    assert {category.id for category in serialized.categories} == {
        category.id for category in categories
    }


@pytest.mark.asyncio
async def test_duplicate_scope_does_not_leak_another_users_metadata(
    session: AsyncSession,
    tmp_path,
    monkeypatch,
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "upload_storage_path", tmp_path)
    owner = await _create_user(session, "owner")
    other_user = await _create_user(session, "other")
    owner_category = (await _create_categories(session, 1))[0]

    owner_service = UploadService(session)
    owner_service.dify.create_document_by_file = AsyncMock(
        return_value={"document": {"id": str(uuid4())}}
    )
    owner_response = await owner_service.create_upload(
        owner.id,
        _file(b"cross-user scoped duplicate"),
        [owner_category.id],
    )

    other_service = UploadService(session)
    other_service.dify.create_document_by_file = AsyncMock(
        return_value={"document": {"id": str(uuid4())}}
    )
    other_response = await other_service.create_upload(
        other_user.id,
        _file(b"cross-user scoped duplicate"),
    )

    assert other_response.id != owner_response.id
    assert await session.scalar(
        select(func.count())
        .select_from(Upload)
        .where(Upload.uploaded_by.in_([owner.id, other_user.id]))
    ) == 2
    other_upload = await other_service.uploads.get_by_id(
        UUID(other_response.id),
        uploaded_by=other_user.id,
    )
    assert other_upload is not None
    assert other_upload.categories == []
    assert await session.scalar(
        select(func.count())
        .select_from(upload_categories)
        .where(upload_categories.c.upload_id == other_upload.id)
    ) == 0
