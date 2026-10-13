from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_super_admin
from app.core.config import settings
from app.db.session import get_db_session
from app.ingestion.service import (
    IngestionJobNotFound,
    InvalidIngestionTransition,
    retry_owned_job,
)
from app.models.site_article import SiteArticle, SiteArticleSyncState
from app.models.upload import Upload
from app.schemas.auth import CurrentUserResponse

router = APIRouter()
Session = Annotated[AsyncSession, Depends(get_db_session)]


@router.get("")
async def list_site_articles(
    current_user: Annotated[CurrentUserResponse, Depends(get_current_user)],
    session: Session,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    del current_user
    selected_upload = func.coalesce(SiteArticle.pending_upload_id, SiteArticle.active_upload_id)
    query = select(SiteArticle, Upload).outerjoin(Upload, Upload.id == selected_upload)
    live = SiteArticle.source_status == "published"
    current_revision = SiteArticle.ingested_modified_at.is_not_distinct_from(
        SiteArticle.modified_at
    )
    failed_condition = live & (
        Upload.status.in_(["failed", "enqueue_failed"]) | SiteArticle.error_message.is_not(None)
    )
    totals = (
        await session.execute(
            select(
                func.count(SiteArticle.id),
                func.sum(
                    case(
                        (
                            live
                            & current_revision
                            & (Upload.status == "indexed")
                            & SiteArticle.error_message.is_(None),
                            1,
                        ),
                        else_=0,
                    )
                ),
                func.sum(case((failed_condition, 1), else_=0)),
                func.sum(case((SiteArticle.source_status == "archived", 1), else_=0)),
            )
            .select_from(SiteArticle)
            .outerjoin(Upload, Upload.id == selected_upload)
        )
    ).one()
    total, indexed, failed, archived = (int(value or 0) for value in totals)
    page = min(page, max(1, (total + limit - 1) // limit))
    rows = (
        await session.execute(
            query.order_by(
                SiteArticle.published_at.desc().nullslast(), SiteArticle.external_id.desc()
            )
            .offset((page - 1) * limit)
            .limit(limit)
        )
    ).all()
    state = await session.get(SiteArticleSyncState, "haminyar")
    items = []
    for article, upload in rows:
        stored = upload is not None and upload.deleted_at is None
        waiting_update = (
            article.active_upload_id is not None
            and article.pending_upload_id is None
            and article.ingested_modified_at != article.modified_at
        )
        items.append(
            {
                "id": str(article.id),
                "external_id": article.external_id,
                "title": article.title,
                "source_url": article.source_url,
                "published_at": article.published_at,
                "modified_at": article.modified_at,
                "source_status": article.source_status,
                "received_at": article.received_at,
                "revision": article.revision,
                "upload_id": str(upload.id) if upload else None,
                "filename": upload.original_filename if upload else None,
                "delivery_status": "stored"
                if stored
                else ("failed" if article.error_message or upload else "pending"),
                "dify_status": ("update_pending" if waiting_update else upload.status)
                if stored
                else "not_started",
                "dify_completed_at": article.dify_completed_at
                if not article.pending_upload_id and not waiting_update
                else None,
                "error_message": article.error_message
                or (
                    "نمایه‌سازی مقاله ناموفق است؛ مدیر می‌تواند دوباره تلاش کند."
                    if upload and upload.status in {"failed", "enqueue_failed"}
                    else None
                ),
            }
        )
    return {
        "items": items,
        "total": total,
        "page": page,
        "limit": limit,
        "summary": {
            "indexed": indexed,
            "failed": failed,
            "archived": archived,
            "pending": max(0, total - indexed - failed - archived),
        },
        "sync": {
            "enabled": settings.site_article_sync_enabled,
            "interval_minutes": 5,
            "last_attempt_at": state.last_attempt_at if state else None,
            "last_success_at": state.last_success_at if state else None,
            "source_article_count": state.source_article_count if state else None,
            "error_message": state.error_message if state else None,
        },
    }


@router.post("/{article_id}/retry")
async def retry_site_article(
    article_id: UUID,
    current_user: Annotated[CurrentUserResponse, Depends(require_super_admin)],
    session: Session,
) -> dict:
    article = await session.get(SiteArticle, article_id)
    if article is None or not article.pending_upload_id:
        raise HTTPException(404, "No pending article ingestion")
    try:
        job = await retry_owned_job(
            session, article.pending_upload_id, UUID(current_user.id), allow_all=True
        )
    except IngestionJobNotFound as exc:
        raise HTTPException(404, "Article ingestion was not found") from exc
    except InvalidIngestionTransition as exc:
        raise HTTPException(409, "Article ingestion is not retryable") from exc
    return {"status": job.status, "upload_id": str(job.upload_id)}
