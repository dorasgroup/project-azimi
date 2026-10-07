"""Composition root for crawler worker operations."""

import asyncio
from datetime import date

from app.bootstrap.serialization import json_ready
from app.modules.crawling.api import BuildCrawlRequest, CrawlSource, ProbeSource
from app.modules.crawling.domain import CrawlOutcome
from app.modules.crawling.ports import SourceCrawler


async def probe_source(crawler: SourceCrawler, url: str) -> dict[str, object]:
    result = await ProbeSource(crawler).execute(url)
    return json_ready(result)  # type: ignore[return-value]


async def crawl_source(
    crawler: SourceCrawler,
    source_url: str,
    topic: str,
    policy: dict[str, object] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, object]:
    request = BuildCrawlRequest().execute(
        source_url=source_url,
        topic=topic,
        policy=policy,
        date_from=date.fromisoformat(date_from) if date_from else None,
        date_to=date.fromisoformat(date_to) if date_to else None,
    )
    return _public_crawl_result(await CrawlSource(crawler).execute(request))


def _public_crawl_result(result: CrawlOutcome) -> dict[str, object]:
    """Keep the established CrawlBatchResult task contract (without internal errors)."""

    return {
        "source_url": result.source_url,
        "probe": json_ready(result.probe),
        "discovered_links": json_ready(result.discovered_links),
        "pages": json_ready(result.pages),
        "started_at": result.started_at.isoformat(),
        "finished_at": result.finished_at.isoformat(),
    }


def _crawler() -> SourceCrawler:
    from app.modules.crawling.infrastructure import Crawl4AiSourceCrawler

    return Crawl4AiSourceCrawler()


def run_probe_source(url: str) -> dict[str, object]:
    return asyncio.run(probe_source(_crawler(), url))


def run_crawl_source(
    source_url: str,
    topic: str,
    policy: dict[str, object] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, object]:
    return asyncio.run(crawl_source(_crawler(), source_url, topic, policy, date_from, date_to))
