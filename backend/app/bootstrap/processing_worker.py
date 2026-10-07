"""Composition root for document-processing worker operations."""

import asyncio
from collections.abc import Mapping
from datetime import date

from app.bootstrap.serialization import json_ready
from app.modules.crawling.api import BuildCrawlRequest, CrawlSource
from app.modules.crawling.domain import CrawledPageRecord
from app.modules.crawling.ports import SourceCrawler
from app.modules.documents.api import PrepareDocuments
from app.modules.documents.domain import DocumentPreparationRequest, RawDocumentPage
from app.modules.documents.ports import DocumentProcessor


def _raw_page(page: Mapping[str, object]) -> RawDocumentPage:
    metadata = page.get("metadata", {})
    return RawDocumentPage(
        url=str(page["url"]),
        final_url=str(page["final_url"]) if page.get("final_url") else None,
        success=bool(page.get("success")),
        status_code=page.get("status_code") if isinstance(page.get("status_code"), int) else None,
        title=str(page["title"]) if page.get("title") is not None else None,
        html=str(page["html"]) if page.get("html") is not None else None,
        markdown=str(page["markdown"]) if page.get("markdown") is not None else None,
        content_hash=(str(page["content_hash"]) if page.get("content_hash") is not None else None),
        cache_status=(str(page["cache_status"]) if page.get("cache_status") is not None else None),
        error=str(page["error"]) if page.get("error") is not None else None,
        metadata=dict(metadata) if isinstance(metadata, Mapping) else {},
    )


def _crawled_page(page: CrawledPageRecord) -> RawDocumentPage:
    return RawDocumentPage(
        url=page.url,
        final_url=page.final_url,
        success=page.success,
        status_code=page.status_code,
        title=page.title,
        html=page.html,
        markdown=page.markdown,
        content_hash=page.content_hash,
        cache_status=page.cache_status,
        error=page.error,
        metadata=page.metadata,
    )


def prepare_pages(
    processor: DocumentProcessor,
    pages: list[dict[str, object]],
    topic: str,
    source_name: str | None = None,
    policy: dict[str, object] | None = None,
) -> dict[str, object]:
    request = DocumentPreparationRequest(
        pages=tuple(_raw_page(page) for page in pages),
        topic=topic,
        source_name=source_name,
        date_from=None,
        date_to=None,
        policy=dict(policy or {}),
    )
    return json_ready(PrepareDocuments(processor).execute(request))  # type: ignore[return-value]


async def crawl_and_prepare_source(
    crawler: SourceCrawler,
    processor: DocumentProcessor,
    source_url: str,
    topic: str,
    source_name: str | None = None,
    crawl_policy: dict[str, object] | None = None,
    processing_policy: dict[str, object] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, object]:
    crawl_request = BuildCrawlRequest().execute(
        source_url=source_url,
        topic=topic,
        policy=crawl_policy,
        date_from=date.fromisoformat(date_from) if date_from else None,
        date_to=date.fromisoformat(date_to) if date_to else None,
    )
    crawl_result = await CrawlSource(crawler).execute(crawl_request)
    preparation_request = DocumentPreparationRequest(
        pages=tuple(_crawled_page(page) for page in crawl_result.pages),
        topic=topic,
        source_name=source_name,
        date_from=None,
        date_to=None,
        policy=dict(processing_policy or {}),
    )
    return json_ready(PrepareDocuments(processor).execute(preparation_request))  # type: ignore[return-value]


def _crawler() -> SourceCrawler:
    from app.modules.crawling.infrastructure import Crawl4AiSourceCrawler

    return Crawl4AiSourceCrawler()


def _processor() -> DocumentProcessor:
    from app.modules.documents.infrastructure import ContentProcessorAdapter

    return ContentProcessorAdapter()


def run_prepare_pages(
    pages: list[dict[str, object]],
    topic: str,
    source_name: str | None = None,
    policy: dict[str, object] | None = None,
) -> dict[str, object]:
    return prepare_pages(_processor(), pages, topic, source_name, policy)


def run_crawl_and_prepare_source(
    source_url: str,
    topic: str,
    source_name: str | None = None,
    crawl_policy: dict[str, object] | None = None,
    processing_policy: dict[str, object] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, object]:
    return asyncio.run(
        crawl_and_prepare_source(
            _crawler(),
            _processor(),
            source_url,
            topic,
            source_name,
            crawl_policy,
            processing_policy,
            date_from,
            date_to,
        )
    )
