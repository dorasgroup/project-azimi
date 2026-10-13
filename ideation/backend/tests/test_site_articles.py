import asyncio
import gzip
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v1.site_articles import list_site_articles
from app.core.config import settings
from app.db.base import Base
from app.integrations.haminyar import sync as sync_entrypoint
from app.integrations.haminyar.service import (
    ingest_content,
    reconcile_article,
    revision_key,
    synchronize,
)
from app.integrations.haminyar.source import (
    ArticleContent,
    ArticleMetadata,
    HaminyarArticleSource,
    SourceUnavailable,
    is_article,
    plain_text,
    public_url,
)
from app.models import Category, SiteArticle, Upload, User
from app.repositories.upload_repository import UploadRepository
from app.schemas.auth import CurrentUserResponse
from app.services.chat_service import ChatService
from app.services.upload_service import UploadService


def post(post_id: int = 42, **updates) -> dict:
    result = {
        "id": post_id,
        "slug": "marriage-article",
        "status": "publish",
        "format": "standard",
        "categories": [3],
        "title": {"rendered": "مقاله ازدواج &amp; خانواده"},
        "excerpt": {"rendered": "خلاصه مقاله"},
        "content": {"rendered": "<p>متن کامل و واقعی مقاله برای بررسی مهارت‌های ازدواج.</p>"},
        "link": "https://haminyar.ir/marriage-guides/marriage-article",
        "date_gmt": "2026-10-01T10:00:00",
        "modified_gmt": "2026-10-01T10:00:00",
    }
    result.update(updates)
    return result


@pytest.mark.parametrize(
    "updates",
    [
        {"status": "draft"},
        {"status": "private"},
        {"format": "video"},
        {"format": "audio"},
        {"categories": [94]},
        {"title": {"rendered": "پادکست ازدواج"}},
    ],
)
def test_only_published_magazine_articles(updates: dict) -> None:
    assert is_article(post())
    assert not is_article(post(**updates))


def test_article_text_excludes_executable_and_embedded_content() -> None:
    assert (
        plain_text("<p>اول &amp; دوم</p><script>secret</script><iframe>nav</iframe><p>سوم</p>")
        == "اول & دوم\nسوم"
    )


@pytest.mark.parametrize(
    "link",
    ["http://internal.local/x", "https://evil.example/x", "https://root:password@haminyar.ir/x"],
)
def test_untrusted_links_are_not_requested(link: str) -> None:
    assert public_url(post(link=link)) == "https://haminyar.ir/marriage-guides/marriage-article"
    with pytest.raises(SourceUnavailable):
        public_url(post(link=link, slug="..%2Fprivate"))


def test_rendered_file_and_hash_are_deterministic_and_safe() -> None:
    metadata = ArticleMetadata.from_post(post(title={"rendered": "ازدواج / خانواده"}))
    content = ArticleContent(metadata, "متن آزمایش ازدواج و خانواده")
    assert content.content_hash == ArticleContent(metadata, content.text).content_hash
    assert content.content_hash != ArticleContent(metadata, content.text + " تغییر").content_hash
    assert content.filename.endswith(".txt") and "/" not in content.filename
    assert "دسته: ازدواج" in content.render_txt().decode()
    assert metadata.published_at.tzinfo is UTC


@pytest.mark.asyncio
async def test_inventory_fetches_all_pages_and_filters_media() -> None:
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        page = int(request.url.params.get("page", 1))
        data = [post(i) for i in range(1, 51)] if page == 1 else [post(51, format="video")]
        return httpx.Response(200, json=data, headers={"X-WP-Total": "51", "X-WP-TotalPages": "2"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await HaminyarArticleSource(client).inventory()
    assert len(result) == 50 and len(requests) == 2
    assert all(request.url.host == "cms.haminyar.ir" for request in requests)


@pytest.mark.asyncio
async def test_partial_inventory_never_returns_a_successful_snapshot() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, json=[post()], headers={"X-WP-Total": "50", "X-WP-TotalPages": "1"}
            )
        )
    ) as client:
        with pytest.raises(SourceUnavailable):
            await HaminyarArticleSource(client).inventory()


@pytest.mark.asyncio
async def test_compressed_cms_response_is_decoded_only_once() -> None:
    import json

    body = gzip.compress(json.dumps([post()]).encode())
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                content=body,
                headers={"content-encoding": "gzip", "X-WP-Total": "1", "X-WP-TotalPages": "1"},
            )
        )
    ) as client:
        result = await HaminyarArticleSource(client).inventory()
    assert result[0].external_id == 42


@pytest.mark.asyncio
async def test_duplicate_inventory_ids_are_rejected() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, json=[post(), post()], headers={"X-WP-Total": "2", "X-WP-TotalPages": "1"}
            )
        )
    ) as client:
        with pytest.raises(SourceUnavailable):
            await HaminyarArticleSource(client).inventory()


@pytest.mark.asyncio
async def test_nonpublic_detail_and_redirects_are_rejected() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=post(status="draft")))
    ) as client:
        with pytest.raises(SourceUnavailable):
            await HaminyarArticleSource(client).content(42)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(302, headers={"Location": "http://127.0.0.1/private"})
        )
    ) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await HaminyarArticleSource(client).content(42)


@pytest_asyncio.fixture
async def db(monkeypatch, tmp_path):
    url = os.getenv("TEST_DATABASE_URL")
    if not url or "articles_test" not in url:
        pytest.skip("isolated articles_test PostgreSQL database required")
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(settings, "async_ingestion_enabled", True)
    monkeypatch.setattr(settings, "upload_storage_path", tmp_path)
    monkeypatch.setattr("app.services.upload_service.publish_ingestion_job", AsyncMock())
    async with maker() as session:
        yield session
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def seed(db):
    owner = User(
        email="article-owner@example.com",
        full_name="مالک اتصال",
        role="super_admin",
        hashed_password="disabled",
        is_active=True,
    )
    ordinary = User(
        email="article-reader@example.com",
        full_name="کاربر",
        role="user",
        hashed_password="disabled",
        is_active=True,
    )
    marriage = Category(name="ازدواج")
    other = Category(name="کنشگری زنان")
    db.add_all([owner, ordinary, marriage, other])
    await db.commit()
    metadata = ArticleMetadata.from_post(post())
    article = SiteArticle(
        external_id=42,
        title=metadata.title,
        source_url=metadata.source_url,
        published_at=metadata.published_at,
        modified_at=metadata.modified_at,
        source_status="published",
        revision=0,
        last_seen_at=datetime.now(UTC),
    )
    db.add(article)
    await db.commit()
    return owner, ordinary, marriage, other, article


@pytest.mark.asyncio
async def test_pipeline_idempotency_revision_and_completed_promotion(db, monkeypatch):
    owner, _, marriage, _, article = await seed(db)
    content = ArticleContent(ArticleMetadata.from_post(post()), "متن مقاله برای آزمون نسخه اول")
    assert await ingest_content(db, article, content, owner_id=owner.id, category_id=marriage.id)
    first_id = article.pending_upload_id
    assert article.active_upload_id is None
    assert not await ingest_content(
        db, article, content, owner_id=owner.id, category_id=marriage.id
    )
    first = await UploadRepository(db).get_by_id(first_id)
    assert first.source == "haminyar_article" and first.categories[0].id == marriage.id
    first.status, first.dify_document_id = "indexed", uuid4()
    await db.commit()
    await reconcile_article(db, article)
    assert article.active_upload_id == first_id and article.pending_upload_id is None
    assert not await ingest_content(
        db, article, content, owner_id=owner.id, category_id=marriage.id
    )
    changed = ArticleContent(content.metadata, content.text + " نسخه جدید")
    assert await ingest_content(db, article, changed, owner_id=owner.id, category_id=marriage.id)
    second = await db.get(Upload, article.pending_upload_id)
    assert article.active_upload_id == first_id and article.revision == 2
    second.status, second.dify_document_id = "indexed", uuid4()
    await db.commit()
    delete = AsyncMock()
    monkeypatch.setattr("app.integrations.haminyar.service.DifyService.delete_document", delete)
    await reconcile_article(db, article)
    assert article.active_upload_id == second.id and first.deleted_at is not None
    assert first.status == "superseded" and article.retired_upload_id is None
    delete.assert_awaited_once_with(str(first.dify_document_id))
    assert (await db.scalar(select(func.count()).select_from(Upload))) == 2
    monkeypatch.setattr(
        "app.services.upload_service.DifyService.list_documents",
        AsyncMock(
            return_value={
                "data": [
                    {
                        "id": str(first.dify_document_id),
                        "name": "old.txt",
                        "indexing_status": "completed",
                    }
                ]
            }
        ),
    )
    await UploadService(db).sync_with_dify(owner.id)
    assert (await db.scalar(select(func.count()).select_from(Upload))) == 2
    assert first.status == "superseded"
    assert not await UploadRepository(db).list_all(exclude_trend_imports=True)


@pytest.mark.asyncio
async def test_crash_reserved_revision_resumes_without_freezing_old_content(db):
    owner, _, marriage, _, article = await seed(db)
    content = ArticleContent(ArticleMetadata.from_post(post()), "متن نسخه نخست")
    await ingest_content(db, article, content, owner_id=owner.id, category_id=marriage.id)
    first = await db.get(Upload, article.pending_upload_id)
    first.status = "indexed"
    await db.commit()
    await reconcile_article(db, article)
    changed = ArticleContent(content.metadata, "متن نسخه دوم تغییر کرده است")
    article.revision, article.content_sha256 = 2, changed.content_hash
    await db.commit()  # simulate crash after key reservation but before file write
    assert await ingest_content(db, article, changed, owner_id=owner.id, category_id=marriage.id)
    second = await db.get(Upload, article.pending_upload_id)
    assert article.revision == 2 and second.source_key == revision_key(article)


@pytest.mark.asyncio
async def test_regular_chat_only_shares_current_public_article_in_selected_repository(
    db, monkeypatch
):
    owner, reader, marriage, other, article = await seed(db)
    content = ArticleContent(
        ArticleMetadata.from_post(post()), "متن دانشنامه ازدواج برای همه کاربران"
    )
    await ingest_content(db, article, content, owner_id=owner.id, category_id=marriage.id)
    shared = await db.get(Upload, article.pending_upload_id)
    shared.status, shared.dify_document_id = "indexed", uuid4()
    await db.commit()
    await reconcile_article(db, article)
    private = Upload(
        uploaded_by=owner.id,
        original_filename="private.txt",
        stored_filename="p.txt",
        storage_path="/private",
        file_extension="txt",
        mime_type="text/plain",
        file_size_bytes=99,
        sha256_hash="f" * 64,
        status="indexed",
        source="web",
        dify_document_id=uuid4(),
        categories=[marriage],
    )
    db.add(private)
    await db.commit()
    retrieve = AsyncMock(return_value=[])
    monkeypatch.setattr("app.services.chat_service.DifyService.retrieve_chunks_filtered", retrieve)
    await ChatService(db).ask(reader.id, "سؤال ازدواج", None, None, [marriage.id])
    assert retrieve.await_args.kwargs["document_ids"] == [str(shared.dify_document_id)]
    retrieve.reset_mock()
    await ChatService(db).ask(reader.id, "سؤال", None, None, [other.id])
    retrieve.assert_not_awaited()
    article.source_status = "archived"
    await db.commit()
    await ChatService(db).ask(
        reader.id, "سؤال", None, None, [marriage.id], allow_all_documents=True
    )
    assert str(shared.dify_document_id) not in retrieve.await_args.kwargs["document_ids"]


@pytest.mark.asyncio
async def test_list_has_true_totals_page_clamping_and_separate_pending_status(db):
    owner, _, marriage, _, article = await seed(db)
    content = ArticleContent(ArticleMetadata.from_post(post()), "متن آزمایشی برای صفحه‌بندی")
    await ingest_content(db, article, content, owner_id=owner.id, category_id=marriage.id)
    for i in range(1, 145):
        db.add(
            SiteArticle(
                external_id=1000 + i,
                title=f"مقاله {i}",
                source_url="https://haminyar.ir/marriage-guides/test",
                source_status="published",
                revision=0,
                published_at=datetime.now(UTC) + timedelta(seconds=i),
                last_seen_at=datetime.now(UTC),
            )
        )
    await db.commit()
    actor = CurrentUserResponse(
        id=str(owner.id),
        email=owner.email,
        full_name=owner.full_name,
        role=owner.role,
        is_active=True,
    )
    result = await list_site_articles(actor, db, page=1, limit=20)
    assert result["total"] == 145 and len(result["items"]) == 20
    assert result["summary"] == {"indexed": 0, "pending": 145, "failed": 0, "archived": 0}
    result = await list_site_articles(actor, db, page=99, limit=20)
    assert result["page"] == 8 and len(result["items"]) == 5
    item = next(item for item in result["items"] if item["external_id"] == 42)
    assert item["delivery_status"] == "stored" and item["dify_status"] == "queued"


@pytest.mark.asyncio
async def test_mixed_source_failure_does_not_block_other_articles(db, monkeypatch):
    owner, _, _, _, _ = await seed(db)
    monkeypatch.setattr(settings, "site_article_user_id", owner.id)
    metadata = [ArticleMetadata.from_post(post()), ArticleMetadata.from_post(post(43))]
    monkeypatch.setattr(HaminyarArticleSource, "inventory", AsyncMock(return_value=metadata))
    monkeypatch.setattr(
        HaminyarArticleSource,
        "content",
        AsyncMock(
            side_effect=[
                SourceUnavailable("temporary failure"),
                ArticleContent(metadata[1], "متن سالم برای آزمون"),
            ]
        ),
    )
    result = await synchronize(db)
    assert result["queued"] == 1 and result["failed"] == 1
    articles = list((await db.scalars(select(SiteArticle).order_by(SiteArticle.external_id))).all())
    assert articles[0].error_message and articles[1].pending_upload_id


@pytest.mark.asyncio
async def test_failed_inventory_never_archives_existing_article(db, monkeypatch):
    owner, _, _, _, article = await seed(db)
    monkeypatch.setattr(settings, "site_article_user_id", owner.id)
    article_id = article.id
    monkeypatch.setattr(
        HaminyarArticleSource,
        "inventory",
        AsyncMock(side_effect=SourceUnavailable("incomplete inventory")),
    )
    with pytest.raises(SourceUnavailable):
        await synchronize(db)
    article = await db.get(SiteArticle, article_id)
    assert article.source_status == "published"


@pytest.mark.asyncio
async def test_changed_source_is_pending_not_falsely_indexed(db):
    owner, _, marriage, _, article = await seed(db)
    content = ArticleContent(ArticleMetadata.from_post(post()), "متن مقاله برای بررسی تغییرات")
    await ingest_content(db, article, content, owner_id=owner.id, category_id=marriage.id)
    upload = await db.get(Upload, article.pending_upload_id)
    upload.status = "indexed"
    await db.commit()
    await reconcile_article(db, article)
    article.modified_at += timedelta(minutes=1)
    await db.commit()
    actor = CurrentUserResponse(
        id=str(owner.id),
        email=owner.email,
        full_name=owner.full_name,
        role=owner.role,
        is_active=True,
    )
    result = await list_site_articles(actor, db, page=1, limit=20)
    assert result["summary"]["indexed"] == 0 and result["summary"]["pending"] == 1
    assert result["items"][0]["dify_status"] == "update_pending"
    assert result["items"][0]["dify_completed_at"] is None


@pytest.mark.asyncio
async def test_single_writer_lock_survives_commits_and_is_released(db, monkeypatch):
    monkeypatch.setattr(settings, "site_article_sync_enabled", True)
    maker = async_sessionmaker(db.bind, expire_on_commit=False)
    monkeypatch.setattr(sync_entrypoint, "get_sessionmaker", lambda: maker)
    started, release = asyncio.Event(), asyncio.Event()

    async def slow_sync(session, **kwargs):
        await session.commit()
        started.set()
        await release.wait()
        return {"queued": 1}

    monkeypatch.setattr(sync_entrypoint, "synchronize", slow_sync)
    first = asyncio.create_task(sync_entrypoint.run(50))
    await asyncio.wait_for(started.wait(), 3)
    assert await sync_entrypoint.run(50) == {"status": "already_running"}
    release.set()
    assert await first == {"queued": 1}
    assert await sync_entrypoint.run(50) == {"queued": 1}
