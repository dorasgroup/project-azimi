import asyncio
import logging
from datetime import UTC, datetime
from io import BytesIO
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import Headers, UploadFile

from app.core.config import settings
from app.ingestion.service import latest_job_for_upload, refresh_dify_job_status
from app.integrations.haminyar.source import ArticleContent, HaminyarArticleSource
from app.models.category import Category
from app.models.site_article import SiteArticle, SiteArticleSyncState
from app.models.upload import Upload
from app.models.user import User
from app.services.dify_service import DifyService
from app.services.upload_service import UploadOrigin, UploadService

logger = logging.getLogger(__name__)
SOURCE = "haminyar_article"


def revision_key(article: SiteArticle) -> str:
    return f"haminyar:{article.external_id}:r{article.revision}:{article.content_sha256}"


async def _cleanup_retired(session: AsyncSession, article: SiteArticle) -> None:
    if not article.retired_upload_id:
        return
    old = await session.get(Upload, article.retired_upload_id)
    if old is None or old.source != SOURCE:
        raise RuntimeError("invalid retired article revision")
    # Local revision files remain recoverable. Delete only this connector's old
    # Dify document, after the replacement was confirmed completed.
    if old.deleted_at is None:
        old.deleted_at = datetime.now(UTC)
        old.status = "superseded"
        await session.commit()
    if old.dify_document_id:
        try:
            await DifyService().delete_document(str(old.dify_document_id))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
    article.retired_upload_id = None
    await session.commit()


async def reconcile_article(session: AsyncSession, article: SiteArticle) -> None:
    if article.pending_upload_id:
        upload = await session.get(Upload, article.pending_upload_id)
        if upload is None or upload.deleted_at is not None:
            article.error_message = "فایل مقاله در سامانه در دسترس نیست؛ بررسی مدیر لازم است."
            await session.commit()
            return
        job = await latest_job_for_upload(session, upload.id)
        if job:
            await refresh_dify_job_status(session, job)
        if upload.status == "indexed":
            article.retired_upload_id = article.active_upload_id
            article.active_upload_id = upload.id
            article.pending_upload_id = None
            article.dify_completed_at = (
                job.completed_at if job and job.completed_at else datetime.now(UTC)
            )
            article.error_message = None
            await session.commit()
    await _cleanup_retired(session, article)


async def ingest_content(
    session: AsyncSession,
    article: SiteArticle,
    content: ArticleContent,
    *,
    owner_id: UUID,
    category_id: UUID,
) -> bool:
    if article.pending_upload_id or article.retired_upload_id:
        return False
    active = (
        await session.get(Upload, article.active_upload_id) if article.active_upload_id else None
    )
    if (
        article.content_sha256 == content.content_hash
        and active
        and active.source_key == revision_key(article)
    ):
        # Timestamp-only changes do not create duplicate knowledge.
        article.ingested_modified_at = content.metadata.modified_at
        article.error_message = None
        await session.commit()
        return False
    if article.content_sha256 != content.content_hash:
        article.revision += 1
        article.content_sha256 = content.content_hash
        # Reserve before filesystem/queue operations. A crash resumes with the
        # same key, including the case where an old active revision still exists.
        await session.commit()
    file = UploadFile(
        BytesIO(content.render_txt()),
        filename=content.filename,
        headers=Headers({"content-type": "text/plain"}),
    )
    try:
        response = await UploadService(session).create_upload(
            owner_id,
            file,
            [category_id],
            request_id=f"haminyar-{article.external_id}-r{article.revision}",
            origin=UploadOrigin(
                source=SOURCE,
                source_key=revision_key(article),
                classification_method="source_category",
                classification_version="haminyar-v1",
                classification_score=1.0,
                classification_needs_review=False,
            ),
        )
    finally:
        await file.close()
    article.pending_upload_id = UUID(response.id)
    article.ingested_modified_at = content.metadata.modified_at
    article.received_at = article.received_at or datetime.now(UTC)
    article.error_message = None
    await session.commit()
    return True


async def synchronize(session: AsyncSession, *, max_articles: int = 50) -> dict:
    state = await session.get(SiteArticleSyncState, "haminyar")
    if state is None:
        state = SiteArticleSyncState(key="haminyar")
        session.add(state)
    state.last_attempt_at = datetime.now(UTC)
    await session.commit()
    try:
        if not settings.async_ingestion_enabled or not settings.site_article_user_id:
            raise RuntimeError("article queue/owner is not configured")
        owner = await session.get(User, settings.site_article_user_id)
        if owner is None or not owner.is_active or owner.deleted_at is not None:
            raise RuntimeError("article owner is unavailable")
        owner_id = owner.id
        category = (
            await session.execute(
                select(Category).where(Category.name == settings.site_article_category_name)
            )
        ).scalar_one_or_none()
        if category is None:
            raise RuntimeError("marriage repository is unavailable")
        category_id = category.id
        articles = list((await session.scalars(select(SiteArticle))).all())
        by_id = {article.external_id: article for article in articles}
        for article in articles:
            try:
                await reconcile_article(session, article)
            except httpx.HTTPError:
                logger.warning(
                    "haminyar_revision_cleanup_deferred external_id=%s", article.external_id
                )
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(45, connect=15),
            headers={"User-Agent": "Haminyar-Ideation-Sync/1.0"},
        ) as client:
            source = HaminyarArticleSource(client)
            inventory = await source.inventory()
            now = datetime.now(UTC)
            seen: set[int] = set()
            for metadata in inventory:
                seen.add(metadata.external_id)
                article = by_id.get(metadata.external_id)
                if article is None:
                    article = SiteArticle(external_id=metadata.external_id, revision=0)
                    session.add(article)
                    by_id[metadata.external_id] = article
                article.title = metadata.title
                article.source_url = metadata.source_url
                article.published_at = metadata.published_at
                article.modified_at = metadata.modified_at
                article.source_status = "published"
                article.last_seen_at = now
            # Only a fully validated inventory may mark a post unpublished.
            # Keep its stored data, but do not expose it to chat.
            for article in articles:
                if article.external_id not in seen:
                    article.source_status = "archived"
            state.source_article_count = len(inventory)
            await session.commit()
            candidates = [
                by_id[item.external_id]
                for item in inventory
                if not by_id[item.external_id].pending_upload_id
                and not by_id[item.external_id].retired_upload_id
                and (
                    not by_id[item.external_id].active_upload_id
                    or by_id[item.external_id].ingested_modified_at != item.modified_at
                )
            ]
            backlog = len(candidates)
            candidates = candidates[:max_articles]
            semaphore = asyncio.Semaphore(4)

            async def fetch_content(article: SiteArticle) -> ArticleContent | Exception:
                async with semaphore:
                    try:
                        return await source.content(article.external_id)
                    except Exception as exc:
                        return exc

            candidate_ids = [(article.id, article.external_id) for article in candidates]
            fetched = await asyncio.gather(*(fetch_content(article) for article in candidates))
            queued = failed = 0
            for (article_id, external_id), content in zip(candidate_ids, fetched, strict=True):
                article = await session.get(SiteArticle, article_id)
                try:
                    if isinstance(content, Exception):
                        raise content
                    queued += int(
                        await ingest_content(
                            session, article, content, owner_id=owner_id, category_id=category_id
                        )
                    )
                except Exception as exc:
                    await session.rollback()
                    article = await session.get(SiteArticle, article_id)
                    article.error_message = "دریافت یا ذخیره مقاله ناموفق بود؛ دوباره تلاش می‌شود."
                    await session.commit()
                    failed += 1
                    logger.warning(
                        "haminyar_article_import_failed external_id=%s type=%s",
                        external_id,
                        type(exc).__name__,
                    )
        state = await session.get(SiteArticleSyncState, "haminyar")
        state.last_success_at = datetime.now(UTC)
        state.error_message = None
        await session.commit()
        return {
            "source_articles": len(inventory),
            "queued": queued,
            "failed": failed,
            "remaining": max(0, backlog - len(candidates)),
        }
    except Exception as exc:
        await session.rollback()
        state = await session.get(SiteArticleSyncState, "haminyar")
        state.error_message = "همگام‌سازی سایت موقتاً ناموفق است؛ اجرای بعدی دوباره تلاش می‌کند."
        await session.commit()
        logger.warning("haminyar_sync_failed type=%s", type(exc).__name__)
        raise
