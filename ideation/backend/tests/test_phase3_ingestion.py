import asyncio
import os
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.ingestion.publisher import publish_ingestion_job
from app.ingestion.service import (
    IngestionJobNotFound,
    InvalidIngestionTransition,
    _safe_storage_file,
    get_job_status,
    process_ingestion_job,
    retry_owned_job,
)
from app.ingestion.state import JobStatus, transition_allowed
from app.models import IngestionJob, Upload, User
from app.services.upload_service import UploadBusyError, UploadService

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


def test_state_machine_accepts_only_documented_transitions() -> None:
    assert transition_allowed("queued", JobStatus.PROCESSING)
    assert transition_allowed("processing", JobStatus.DOCUMENT_CREATED)
    assert not transition_allowed("processing", JobStatus.INDEXED)
    assert transition_allowed("document_created", JobStatus.INDEXING)
    assert transition_allowed("indexing", JobStatus.INDEXED)
    assert transition_allowed("processing", JobStatus.RETRYING)
    assert transition_allowed("retrying", JobStatus.PROCESSING)
    assert transition_allowed("enqueue_failed", JobStatus.QUEUED)
    assert not transition_allowed("indexed", JobStatus.PROCESSING)
    assert not transition_allowed("failed", JobStatus.QUEUED)
    assert transition_allowed("failed", JobStatus.QUEUED, explicit_retry=True)
    assert not transition_allowed("unknown", JobStatus.PROCESSING)


def test_storage_guard_accepts_only_regular_files_below_root(tmp_path, monkeypatch) -> None:
    root = tmp_path / "uploads"
    root.mkdir()
    safe = root / "safe.txt"
    safe.write_bytes(b"safe")
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside")
    link = root / "link.txt"
    link.symlink_to(outside)
    monkeypatch.setattr(settings, "upload_storage_path", root)

    assert _safe_storage_file(str(safe)) == safe
    with pytest.raises(PermissionError):
        _safe_storage_file(str(outside))
    with pytest.raises(PermissionError):
        _safe_storage_file(str(link))
    with pytest.raises(FileNotFoundError):
        _safe_storage_file(str(root / "missing.txt"))


@pytest.mark.asyncio
async def test_publisher_sends_only_job_id_and_bounded_headers(monkeypatch) -> None:
    captured = {}

    def send_task(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr("app.ingestion.publisher.celery_app.send_task", send_task)
    job_id = uuid4()
    await publish_ingestion_job(job_id, "safe-request-id")

    assert captured["args"][0] == "ideation.ingestion.process.v1"
    assert captured["kwargs"]["args"] == [str(job_id)]
    assert captured["kwargs"]["headers"]["schema_version"] == 1
    assert captured["kwargs"]["headers"]["request_id"] == "safe-request-id"
    serialized = repr(captured).lower()
    for forbidden in ("filename", "storage_path", "file_content", "user_id", "upload_id"):
        assert forbidden not in serialized


@pytest_asyncio.fixture
async def database():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is required")
    engine = create_async_engine(TEST_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _user(session: AsyncSession, label: str) -> User:
    user = User(
        email=f"phase3-{label}-{uuid4()}@example.com",
        full_name="Phase 3 integration",
        hashed_password="not-a-production-credential",
        role="user",
        is_active=True,
    )
    session.add(user)
    await session.commit()
    return user


def _file(content: bytes) -> UploadFile:
    return UploadFile(filename="phase3.txt", file=BytesIO(content))


@pytest.mark.asyncio
async def test_async_upload_creates_one_job_and_duplicate_reuses_it(
    database,
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "async_ingestion_enabled", True)
    monkeypatch.setattr(settings, "upload_storage_path", tmp_path)
    publish = AsyncMock()
    monkeypatch.setattr("app.services.upload_service.publish_ingestion_job", publish)
    async with database() as session:
        user = await _user(session, "job")
        service = UploadService(session)
        service.dify.create_document_by_file = AsyncMock()
        first = await service.create_upload(user.id, _file(b"one queued document"))
        duplicate = await service.create_upload(user.id, _file(b"one queued document"))

        assert first.status == JobStatus.QUEUED
        assert first.ingestion_job_id
        assert duplicate.id == first.id
        assert duplicate.ingestion_job_id == first.ingestion_job_id
        assert publish.await_count == 1
        service.dify.create_document_by_file.assert_not_awaited()
        assert await session.scalar(
            select(func.count()).select_from(Upload).where(Upload.uploaded_by == user.id)
        ) == 1
        assert await session.scalar(
            select(func.count())
            .select_from(IngestionJob)
            .join(Upload)
            .where(Upload.uploaded_by == user.id)
        ) == 1


@pytest.mark.asyncio
async def test_concurrent_duplicate_is_serialized_by_postgres_lock(
    database,
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "async_ingestion_enabled", True)
    monkeypatch.setattr(settings, "upload_storage_path", tmp_path)
    publish = AsyncMock()
    monkeypatch.setattr("app.services.upload_service.publish_ingestion_job", publish)
    async with database() as setup_session:
        user = await _user(setup_session, "concurrent")

    async def create_one():
        async with database() as session:
            return await UploadService(session).create_upload(
                user.id,
                _file(b"concurrent identical phase three fixture"),
            )

    first, second = await asyncio.gather(create_one(), create_one())
    assert first.id == second.id
    assert first.ingestion_job_id == second.ingestion_job_id
    assert publish.await_count == 1
    async with database() as session:
        assert await session.scalar(
            select(func.count()).select_from(Upload).where(Upload.uploaded_by == user.id)
        ) == 1


@pytest.mark.asyncio
async def test_worker_waits_for_real_dify_completion_before_marking_indexed(
    database,
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "async_ingestion_enabled", True)
    monkeypatch.setattr(settings, "upload_storage_path", tmp_path)
    monkeypatch.setattr("app.services.upload_service.publish_ingestion_job", AsyncMock())
    monkeypatch.setattr("app.ingestion.service.get_sessionmaker", lambda: database)
    document_id = uuid4()
    dify = AsyncMock(return_value={"document": {"id": str(document_id)}})
    monkeypatch.setattr(
        "app.ingestion.service.DifyService.create_document_by_file",
        dify,
    )
    async with database() as session:
        user = await _user(session, "worker")
        created = await UploadService(session).create_upload(
            user.id,
            _file(b"worker success fixture"),
        )
        job_id = UUID(created.ingestion_job_id)

    assert (
        await process_ingestion_job(job_id, redelivered=False)
        == JobStatus.DOCUMENT_CREATED
    )
    assert dify.await_count == 1
    async with database() as session:
        job = await session.get(IngestionJob, job_id)
        upload = await session.get(Upload, job.upload_id)
        assert job.status == JobStatus.DOCUMENT_CREATED
        assert job.attempt_count == 1
        assert job.completed_at is None
        assert upload.status == JobStatus.DOCUMENT_CREATED
        assert upload.dify_document_id == document_id

        get_document = AsyncMock(return_value={"indexing_status": "indexing"})
        monkeypatch.setattr(
            "app.ingestion.service.DifyService.get_document",
            get_document,
        )
        intermediate = await get_job_status(session, upload.id, user.id)
        assert intermediate.status == JobStatus.INDEXING
        assert intermediate.completed_at is None

        get_document.return_value = {
            "indexing_status": "completed",
            "completed_at": "2026-09-14T07:00:00Z",
        }
        completed = await get_job_status(session, upload.id, user.id)
        assert completed.status == JobStatus.INDEXED
        assert completed.completed_at is not None
        assert get_document.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_status", "expected_code"),
    [("error", "dify_indexing_failed"), ("paused", "dify_indexing_paused")],
)
async def test_provider_error_or_pause_is_never_reported_as_indexed(
    database,
    tmp_path,
    monkeypatch,
    provider_status,
    expected_code,
) -> None:
    monkeypatch.setattr(settings, "async_ingestion_enabled", True)
    monkeypatch.setattr(settings, "upload_storage_path", tmp_path)
    monkeypatch.setattr("app.services.upload_service.publish_ingestion_job", AsyncMock())
    monkeypatch.setattr("app.ingestion.service.get_sessionmaker", lambda: database)
    document_id = uuid4()
    monkeypatch.setattr(
        "app.ingestion.service.DifyService.create_document_by_file",
        AsyncMock(return_value={"document": {"id": str(document_id)}}),
    )
    monkeypatch.setattr(
        "app.ingestion.service.DifyService.get_document",
        AsyncMock(return_value={"indexing_status": provider_status}),
    )
    async with database() as session:
        user = await _user(session, f"provider-{provider_status}")
        created = await UploadService(session).create_upload(
            user.id,
            _file(f"provider {provider_status} fixture".encode()),
        )
        job_id = UUID(created.ingestion_job_id)

    await process_ingestion_job(job_id, redelivered=False)
    async with database() as session:
        status = await get_job_status(session, UUID(created.id), user.id)
        job = await session.get(IngestionJob, job_id)
        assert status.status == JobStatus.FAILED
        assert status.completed_at is None
        assert job.error_code == expected_code


@pytest.mark.asyncio
async def test_missing_file_is_permanent_failure(database, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "async_ingestion_enabled", True)
    monkeypatch.setattr(settings, "upload_storage_path", tmp_path)
    monkeypatch.setattr("app.services.upload_service.publish_ingestion_job", AsyncMock())
    monkeypatch.setattr("app.ingestion.service.get_sessionmaker", lambda: database)
    async with database() as session:
        user = await _user(session, "missing")
        created = await UploadService(session).create_upload(
            user.id,
            _file(b"missing after queue fixture"),
        )
        job_id = UUID(created.ingestion_job_id)
        upload = await session.get(Upload, UUID(created.id))
        Path(upload.storage_path).unlink()

    assert await process_ingestion_job(job_id, redelivered=False) == JobStatus.FAILED
    async with database() as session:
        job = await session.get(IngestionJob, job_id)
        assert job.error_code == "file_missing"
        assert job.error_message == "The stored file is unavailable."


@pytest.mark.asyncio
async def test_status_ownership_retry_guard_and_delete_conflict(
    database,
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "async_ingestion_enabled", True)
    monkeypatch.setattr(settings, "upload_storage_path", tmp_path)
    publish = AsyncMock()
    monkeypatch.setattr("app.services.upload_service.publish_ingestion_job", publish)
    monkeypatch.setattr("app.ingestion.service.publish_ingestion_job", publish)
    async with database() as session:
        owner = await _user(session, "owner")
        other = await _user(session, "other")
        created = await UploadService(session).create_upload(
            owner.id,
            _file(b"status ownership fixture"),
        )
        upload_id = UUID(created.id)
        status = await get_job_status(session, upload_id, owner.id)
        assert status.status == JobStatus.QUEUED
        with pytest.raises(IngestionJobNotFound):
            await get_job_status(session, upload_id, other.id)
        with pytest.raises(InvalidIngestionTransition):
            await retry_owned_job(session, upload_id, owner.id, "retry-request")
        with pytest.raises(UploadBusyError):
            await UploadService(session).delete_upload(upload_id, owner.id)

        job = await session.get(IngestionJob, status.job_id)
        upload = await session.get(Upload, upload_id)
        job.status = JobStatus.ENQUEUE_FAILED
        upload.status = JobStatus.ENQUEUE_FAILED
        await session.commit()
        retried = await retry_owned_job(session, upload_id, owner.id, "retry-request")
        assert retried.status == JobStatus.QUEUED
