import hashlib
import json
from datetime import UTC, datetime
from io import BytesIO
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import Headers

from app.ingestion.service import latest_job_for_upload, refresh_dify_job_status
from app.models import Category
from app.models.trend_import import TrendImport
from app.models.upload import Upload, upload_categories
from app.models.user import User
from app.schemas.trend_integration import (
    TrendImportListResponse,
    TrendImportRequest,
    TrendImportResponse,
)
from app.services.trend_category_service import (
    assign_category_if_empty,
    classify_trend_category,
)
from app.services.upload_service import UploadOrigin, UploadService


class TrendImportConflict(Exception):
    pass


class TrendIntegrationConfigurationError(Exception):
    pass


def canonical_content_hash(payload: TrendImportRequest) -> str:
    serialized = json.dumps(
        payload.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def render_trend_txt(payload: TrendImportRequest) -> bytes:
    lines = [
        "نوع محتوا: ترند سامانه رصد",
        f"سامانه مبدأ: {payload.source_system}",
        f"شناسه ترند: {payload.source_trend_id}",
        f"نسخه: {payload.source_version}",
        f"عنوان: {payload.title}",
        f"موضوع: {payload.topic or '-'}",
        f"وضعیت: {payload.status}",
        f"امتیاز: {payload.score if payload.score is not None else '-'}",
        f"زمان شناسایی: {payload.detected_at.isoformat()}",
        "",
        "خلاصه:",
        payload.summary.strip(),
    ]
    for index, item in enumerate(payload.items, start=1):
        lines.extend(
            [
                "",
                f"--- محتوای مرتبط {index} ---",
                f"منبع: {item.source_name}",
                f"شناسه منبع: {item.external_id}",
                f"نشانی: {item.source_url or '-'}",
                f"زمان انتشار: {item.published_at.isoformat() if item.published_at else '-'}",
                item.text.strip(),
            ]
        )
    return ("\n".join(lines).rstrip() + "\n").encode("utf-8")


async def _find_existing(
    session: AsyncSession,
    payload: TrendImportRequest,
    idempotency_key: str,
) -> TrendImport | None:
    by_key = (
        await session.execute(
            select(TrendImport).where(TrendImport.idempotency_key == idempotency_key)
        )
    ).scalar_one_or_none()
    by_source = (
        await session.execute(
            select(TrendImport).where(
                TrendImport.source_system == payload.source_system,
                TrendImport.source_trend_id == payload.source_trend_id,
                TrendImport.source_version == payload.source_version,
            )
        )
    ).scalar_one_or_none()
    if by_key is not None and by_source is not None and by_key.id != by_source.id:
        raise TrendImportConflict
    return by_key or by_source


async def _to_response(
    session: AsyncSession,
    trend_import: TrendImport,
    *,
    duplicate: bool,
) -> TrendImportResponse:
    upload = (
        await session.get(Upload, trend_import.upload_id)
        if trend_import.upload_id is not None
        else None
    )
    job = (
        await latest_job_for_upload(session, trend_import.upload_id)
        if trend_import.upload_id is not None
        else None
    )
    category_names = (
        list(
            (
                await session.execute(
                    select(Category.name)
                    .join(
                        upload_categories,
                        upload_categories.c.category_id == Category.id,
                    )
                    .where(upload_categories.c.upload_id == trend_import.upload_id)
                    .order_by(Category.name)
                )
            ).scalars()
        )
        if trend_import.upload_id is not None
        else []
    )
    dify_status = trend_import.dify_status
    dify_document_id = None
    if upload is not None:
        dify_document_id = upload.dify_document_id
        dify_status = "document_created" if dify_document_id else upload.status
    if job is not None:
        job = await refresh_dify_job_status(session, job)
        dify_status = job.status
        dify_document_id = job.dify_document_id
    completed_at = job.completed_at if job is not None else None
    changed = (
        trend_import.dify_status != dify_status
        or trend_import.dify_completed_at != completed_at
    )
    if changed:
        trend_import.dify_status = dify_status
        trend_import.dify_completed_at = completed_at
        await session.commit()
    return TrendImportResponse(
        id=trend_import.id,
        source_system=trend_import.source_system,
        source_trend_id=trend_import.source_trend_id,
        source_version=trend_import.source_version,
        content_sha256=trend_import.content_sha256,
        title=trend_import.title,
        topic=trend_import.topic,
        category_names=category_names,
        trend_status=trend_import.trend_status,
        score=trend_import.score,
        detected_at=trend_import.detected_at,
        upload_id=trend_import.upload_id,
        ingestion_job_id=job.id if job is not None else None,
        delivery_status=trend_import.delivery_status,
        dify_status=dify_status,
        dify_document_id=dify_document_id,
        received_at=trend_import.received_at,
        dify_completed_at=trend_import.dify_completed_at,
        duplicate=duplicate,
    )


async def receive_trend(
    session: AsyncSession,
    *,
    payload: TrendImportRequest,
    idempotency_key: str,
    user_id: UUID,
    request_id: str | None,
) -> TrendImportResponse:
    user = await session.get(User, user_id)
    if user is None or not user.is_active or user.deleted_at is not None:
        raise TrendIntegrationConfigurationError

    content_hash = canonical_content_hash(payload)
    existing = await _find_existing(session, payload, idempotency_key)
    if existing is not None:
        if existing.content_sha256 != content_hash:
            raise TrendImportConflict
        if existing.upload_id is not None:
            await assign_category_if_empty(
                session,
                upload_id=existing.upload_id,
                payload=payload,
            )
            return await _to_response(session, existing, duplicate=True)
        trend_import = existing
    else:
        now = datetime.now(UTC)
        trend_import = TrendImport(
            source_system=payload.source_system,
            source_trend_id=payload.source_trend_id,
            source_version=payload.source_version,
            idempotency_key=idempotency_key,
            content_sha256=content_hash,
            title=payload.title,
            topic=payload.topic,
            trend_status=payload.status,
            score=payload.score,
            detected_at=payload.detected_at,
            delivery_status="received",
            dify_status="not_started",
            received_at=now,
            last_attempt_at=now,
        )
        session.add(trend_import)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            trend_import = await _find_existing(session, payload, idempotency_key)
            if trend_import is None or trend_import.content_sha256 != content_hash:
                raise TrendImportConflict from None
            if trend_import.upload_id is not None:
                await assign_category_if_empty(
                    session,
                    upload_id=trend_import.upload_id,
                    payload=payload,
                )
                return await _to_response(session, trend_import, duplicate=True)

    category = await classify_trend_category(session, payload)
    content = render_trend_txt(payload)
    filename = f"trend-{content_hash[:20]}.txt"
    upload = UploadFile(
        file=BytesIO(content),
        filename=filename,
        headers=Headers({"content-type": "text/plain; charset=utf-8"}),
    )
    result = await UploadService(session).create_upload(
        user_id,
        upload,
        category_ids=[category.id] if category is not None else None,
        request_id=request_id,
        origin=UploadOrigin(source="trend"),
    )
    trend_import.upload_id = UUID(result.id)
    trend_import.delivery_status = "stored"
    trend_import.dify_status = result.status
    trend_import.last_attempt_at = datetime.now(UTC)
    trend_import.error_code = None
    trend_import.error_message = None
    await session.commit()
    return await _to_response(session, trend_import, duplicate=False)


async def list_trend_imports(
    session: AsyncSession,
    *,
    limit: int = 100,
) -> TrendImportListResponse:
    imports = list(
        (
            await session.execute(
                select(TrendImport)
                .order_by(TrendImport.received_at.desc())
                .limit(limit)
            )
        ).scalars()
    )
    items = [
        await _to_response(session, trend_import, duplicate=False)
        for trend_import in imports
    ]
    return TrendImportListResponse(items=items, total=len(items))


async def trend_status(
    session: AsyncSession,
    *,
    source_system: str,
    source_trend_id: str,
    source_version: int,
) -> TrendImportResponse | None:
    trend_import = (
        await session.execute(
            select(TrendImport).where(
                TrendImport.source_system == source_system,
                TrendImport.source_trend_id == source_trend_id,
                TrendImport.source_version == source_version,
            )
        )
    ).scalar_one_or_none()
    if trend_import is None:
        return None
    return await _to_response(session, trend_import, duplicate=False)
