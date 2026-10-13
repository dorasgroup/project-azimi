import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from uuid import UUID, uuid4

import httpx
from fastapi import UploadFile
from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.ingestion.publisher import IngestionPublishError, publish_ingestion_job
from app.ingestion.service import is_upload_busy, latest_job_for_upload, mark_enqueue_failed
from app.ingestion.state import JobStatus
from app.models.bale import BaleUpdate
from app.models.category import Category
from app.models.ingestion_job import IngestionJob
from app.models.upload import Upload
from app.observability.metrics import (
    INGESTION_JOBS_CREATED,
    INGESTION_TRANSITIONS,
    UPLOAD_INDEXING_DURATION,
    UPLOAD_INDEXING_RESULTS,
    UPLOAD_SIZE,
    UPLOAD_STORAGE_DURATION,
    UPLOAD_VALIDATION_DURATION,
    observe_upload,
)
from app.repositories.upload_repository import UploadRepository
from app.schemas.upload import UploadResponse
from app.services.dify_service import DifyService
from app.services.file_validation_service import FileValidationService

UPLOAD_SOURCES = {"web", "bale", "trend", "dify_sync", "legacy", "haminyar_article"}


@dataclass(frozen=True)
class UploadOrigin:
    source: str
    source_key: str | None = None
    classification_method: str | None = None
    classification_version: str | None = None
    classification_score: float | None = None
    classification_needs_review: bool = False
    bale_update_id: int | None = None

    def __post_init__(self) -> None:
        if self.source not in UPLOAD_SOURCES:
            raise ValueError("Unsupported upload source")


class UploadService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.uploads = UploadRepository(session)
        self.validator = FileValidationService()
        self.dify = DifyService()

    @observe_upload
    async def create_upload(
        self,
        user_id: UUID,
        file: UploadFile,
        category_ids: list[UUID] | None = None,
        request_id: str | None = None,
        origin: UploadOrigin | None = None,
    ) -> UploadResponse:
        if origin is not None and origin.source_key:
            existing_by_source = await self.uploads.get_by_source_key(origin.source_key)
            if existing_by_source is not None:
                await self._link_bale_update(existing_by_source, origin)
                await self.session.commit()
                job = await latest_job_for_upload(self.session, existing_by_source.id)
                return UploadResponse(
                    id=str(existing_by_source.id),
                    filename=existing_by_source.original_filename,
                    status=job.status if job is not None else existing_by_source.status,
                    ingestion_job_id=str(job.id) if job is not None else None,
                    status_url=(
                        f"/api/v1/uploads/{existing_by_source.id}/ingestion"
                        if job is not None
                        else None
                    ),
                    request_id=request_id,
                    message="This Bale submission has already been stored",
                )

        validation_started = perf_counter()
        extension = self.validator.validate_filename(file.filename)
        UPLOAD_VALIDATION_DURATION.observe(perf_counter() - validation_started)
        storage_root = settings.upload_storage_path
        storage_root.mkdir(parents=True, exist_ok=True)

        categories = []
        if category_ids:
            result = await self.session.execute(
                select(Category).where(Category.id.in_(category_ids))
            )
            categories = list(result.scalars().all())

        digest = hashlib.sha256()
        total_size = 0
        temp_path = storage_root / f"{uuid4()}.tmp"
        storage_started = perf_counter()
        with temp_path.open("wb") as buffer:
            while chunk := await file.read(1024 * 1024):
                total_size += len(chunk)
                if total_size > settings.upload_max_size_bytes:
                    temp_path.unlink(missing_ok=True)
                    self.validator.validate_size(total_size)
                digest.update(chunk)
                buffer.write(chunk)
            buffer.flush()
            os.fsync(buffer.fileno())

        self.validator.validate_size(total_size)
        UPLOAD_SIZE.observe(total_size)
        sha256_hash = digest.hexdigest()
        if settings.async_ingestion_enabled:
            lock_digest = hashlib.sha256(f"{user_id}:{sha256_hash}".encode()).digest()
            lock_key = int.from_bytes(lock_digest[:8], "big", signed=True)
            await self.session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": lock_key},
            )
        existing = await self.uploads.get_by_hash(sha256_hash, user_id)
        if existing:
            temp_path.unlink(missing_ok=True)
            UPLOAD_STORAGE_DURATION.observe(perf_counter() - storage_started)
            changed = False
            if categories and (origin is None or origin.source != "bale"):
                existing.categories = categories
                changed = True
            if origin is not None:
                await self._link_bale_update(existing, origin)
                changed = True
            if settings.async_ingestion_enabled and not self.validator.is_audio(
                existing.file_extension
            ):
                job = await latest_job_for_upload(self.session, existing.id)
                job_created = False
                if job is None and existing.dify_document_id is None:
                    job = IngestionJob(
                        upload_id=existing.id,
                        status=JobStatus.QUEUED,
                        attempt_count=0,
                        max_attempts=settings.ingestion_max_attempts,
                        dify_dataset_id=settings.dify_dataset_id,
                    )
                    self.session.add(job)
                    existing.status = JobStatus.QUEUED
                    existing.error_message = None
                    INGESTION_JOBS_CREATED.inc()
                    INGESTION_TRANSITIONS.labels(JobStatus.QUEUED).inc()
                    changed = True
                    job_created = True
                if changed:
                    await self.session.commit()
                if job_created:
                    try:
                        await publish_ingestion_job(job.id, request_id)
                    except IngestionPublishError:
                        await mark_enqueue_failed(self.session, job.id)
                        job = await latest_job_for_upload(self.session, existing.id)
                return UploadResponse(
                    id=str(existing.id),
                    filename=existing.original_filename,
                    status=job.status if job is not None else existing.status,
                    ingestion_job_id=str(job.id) if job is not None else None,
                    status_url=(
                        f"/api/v1/uploads/{existing.id}/ingestion" if job is not None else None
                    ),
                    request_id=request_id,
                    message="This file already exists; its ingestion state was reused",
                )
            if (
                not self.validator.is_audio(existing.file_extension)
                and existing.dify_document_id is None
            ):
                with UPLOAD_INDEXING_DURATION.time():
                    await self._index_upload_in_dify(existing)
                UPLOAD_INDEXING_RESULTS.labels(
                    "success" if existing.status == "indexed" else "failure"
                ).inc()
                changed = True
            if changed:
                await self.session.commit()
            return UploadResponse(
                id=str(existing.id),
                filename=existing.original_filename,
                status=existing.status,
                message=(
                    "This file already exists and was indexed in Dify"
                    if existing.dify_document_id
                    else "This file has already been uploaded"
                ),
            )

        stored_filename = f"{uuid4()}_{sha256_hash[:12]}{extension}"
        final_path = storage_root / stored_filename
        temp_path.replace(final_path)
        UPLOAD_STORAGE_DURATION.observe(perf_counter() - storage_started)

        status = "stored" if self.validator.is_audio(extension) else "uploaded"
        upload = Upload(
            uploaded_by=user_id,
            categories=categories,
            original_filename=file.filename or stored_filename,
            stored_filename=stored_filename,
            storage_path=str(final_path),
            file_extension=extension,
            mime_type=file.content_type or "application/octet-stream",
            file_size_bytes=total_size,
            sha256_hash=sha256_hash,
            status=status,
            dify_dataset_id=settings.dify_dataset_id,
            source=origin.source if origin is not None else "legacy",
            source_key=origin.source_key if origin is not None else None,
            classification_method=(
                origin.classification_method if origin is not None else None
            ),
            classification_version=(
                origin.classification_version if origin is not None else None
            ),
            classification_score=(
                origin.classification_score if origin is not None else None
            ),
            classification_needs_review=(
                origin.classification_needs_review if origin is not None else False
            ),
        )
        await self.uploads.create(upload)
        if origin is not None:
            await self._link_bale_update(upload, origin)
        if settings.async_ingestion_enabled and not self.validator.is_audio(extension):
            job = IngestionJob(
                upload_id=upload.id,
                status=JobStatus.QUEUED,
                attempt_count=0,
                max_attempts=settings.ingestion_max_attempts,
                dify_dataset_id=settings.dify_dataset_id,
            )
            self.session.add(job)
            upload.status = JobStatus.QUEUED
            await self.session.commit()
            INGESTION_JOBS_CREATED.inc()
            INGESTION_TRANSITIONS.labels(JobStatus.QUEUED).inc()
            try:
                await publish_ingestion_job(job.id, request_id)
            except IngestionPublishError:
                await mark_enqueue_failed(self.session, job.id)
                job = await latest_job_for_upload(self.session, upload.id)
            return UploadResponse(
                id=str(upload.id),
                filename=upload.original_filename,
                status=job.status,
                ingestion_job_id=str(job.id),
                status_url=f"/api/v1/uploads/{upload.id}/ingestion",
                request_id=request_id,
                message=(
                    "File stored and queued for indexing"
                    if job.status == JobStatus.QUEUED
                    else "File stored; queue publication can be retried"
                ),
            )
        if not self.validator.is_audio(extension):
            with UPLOAD_INDEXING_DURATION.time():
                await self._index_upload_in_dify(upload)
            UPLOAD_INDEXING_RESULTS.labels(
                "success" if upload.status == "indexed" else "failure"
            ).inc()
        else:
            UPLOAD_INDEXING_RESULTS.labels("skipped_audio").inc()
        await self.session.commit()
        return UploadResponse(
            id=str(upload.id),
            filename=upload.original_filename,
            status=upload.status,
            ingestion_job_id=None,
            message=self._upload_message(upload),
        )

    async def delete_upload(
        self,
        upload_id: UUID,
        actor_user_id: UUID,
        *,
        allow_all: bool = False,
    ) -> None:
        upload = await self.uploads.get_by_id(
            upload_id,
            uploaded_by=None if allow_all else actor_user_id,
        )
        if upload is None:
            raise FileNotFoundError("Upload not found")

        if is_upload_busy(await latest_job_for_upload(self.session, upload.id)):
            raise UploadBusyError("Upload ingestion is still active")

        if upload.dify_document_id:
            try:
                await self.dify.delete_document(str(upload.dify_document_id))
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 404:
                    raise DifyDocumentDeleteError("Dify document deletion failed") from exc
            except httpx.HTTPError as exc:
                raise DifyDocumentDeleteError("Dify document deletion failed") from exc

        if upload.storage_path:
            storage_path = Path(upload.storage_path)
            if storage_path.exists() and storage_path.is_file():
                storage_path.unlink()

        await self.uploads.soft_delete(upload)
        await self.session.commit()

    async def update_upload_categories(
        self,
        upload_id: UUID,
        category_ids: list[UUID],
        actor_user_id: UUID,
        *,
        allow_all: bool = False,
    ) -> None:
        upload = await self.uploads.get_by_id(
            upload_id,
            uploaded_by=None if allow_all else actor_user_id,
        )
        if upload is None:
            raise FileNotFoundError("Upload not found")

        categories = []
        if category_ids:
            result = await self.session.execute(
                select(Category).where(Category.id.in_(category_ids))
            )
            categories = list(result.scalars().all())

        upload.categories = categories
        await self.session.commit()

    async def sync_with_dify(self, user_id: UUID) -> None:
        try:
            dify_docs = await self.dify.list_documents()
            doc_list = dify_docs.get("data", [])
        except Exception:
            # Safe print/log since print might crash on server log files if cp1252
            return

        if not doc_list:
            return

        result = await self.session.execute(
            select(Upload).where(
                or_(Upload.deleted_at.is_(None), Upload.source == "haminyar_article")
            )
        )
        existing_uploads = {
            upload.dify_document_id: upload
            for upload in result.scalars().all()
            if upload.dify_document_id
        }

        added_any = False
        for doc in doc_list:
            doc_id = UUID(doc["id"])
            indexing_status = str(doc.get("indexing_status") or "").lower()
            if indexing_status == "completed":
                local_status = JobStatus.INDEXED
            elif indexing_status in {"error", "paused"}:
                local_status = JobStatus.FAILED
            else:
                local_status = JobStatus.INDEXING
            if doc_id in existing_uploads:
                existing = existing_uploads[doc_id]
                if existing.deleted_at is not None:
                    # Never recreate/revive a retired article while its Dify
                    # cleanup is being retried by the connector.
                    continue
                if existing.status != local_status:
                    existing.status = local_status
                    added_any = True
                continue

            filename = doc.get("name", "")
            result_by_name = await self.session.execute(
                select(Upload).where(
                    Upload.original_filename == filename,
                    Upload.dify_document_id.is_(None),
                    Upload.deleted_at.is_(None),
                )
            )
            existing_by_name = result_by_name.scalar_one_or_none()
            if existing_by_name:
                existing_by_name.dify_document_id = doc_id
                existing_by_name.status = local_status
                added_any = True
                continue

            detail = doc.get("data_source_detail_dict", {}) or {}
            upload_file = detail.get("upload_file", {}) or {}
            size = upload_file.get("size", 0)
            mime_type = upload_file.get("mime_type", "application/octet-stream")
            ext = Path(filename).suffix if filename else ".bin"

            new_upload = Upload(
                id=doc_id,
                uploaded_by=user_id,
                original_filename=filename,
                stored_filename=filename,
                storage_path="",
                file_extension=ext,
                mime_type=mime_type,
                file_size_bytes=size,
                sha256_hash=f"dify_{doc['id'][:12]}",
                status=local_status,
                dify_dataset_id=settings.dify_dataset_id,
                dify_document_id=doc_id,
                source="dify_sync",
            )
            self.session.add(new_upload)
            added_any = True

        if added_any:
            await self.session.commit()

    async def _link_bale_update(self, upload: Upload, origin: UploadOrigin) -> None:
        if origin.bale_update_id is None:
            return
        update = await self.session.get(BaleUpdate, origin.bale_update_id)
        if update is None:
            raise ValueError("Bale update does not exist")
        update.upload_id = upload.id

    async def _index_upload_in_dify(self, upload: Upload) -> None:
        try:
            result = await self.dify.create_document_by_file(
                file_path=Path(upload.storage_path),
                filename=upload.original_filename,
                mime_type=upload.mime_type,
            )
        except httpx.HTTPError as exc:
            upload.status = "indexing_failed"
            upload.error_message = str(exc)
            return

        document = result.get("document")
        if isinstance(document, dict) and document.get("id"):
            upload.dify_document_id = UUID(str(document["id"]))
        upload.status = "indexed"
        upload.error_message = None

    def _upload_message(self, upload: Upload) -> str:
        if upload.status == "stored":
            return "Audio file stored"
        if upload.status == "indexed":
            return "File uploaded and indexed in Dify"
        if upload.status == "indexing_failed":
            return "File uploaded locally, but Dify indexing failed"
        return "File uploaded"


class DifyDocumentDeleteError(Exception):
    pass


class UploadBusyError(Exception):
    pass
