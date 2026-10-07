"""Compatibility adapter for the established CrawlEngine implementation."""

from app.crawler.engine import CrawlEngine
from app.crawler.schemas import CrawlPolicy, CrawledPage, DiscoveredLink, SourceProbe
from app.modules.crawling.domain import (
    CrawledPageRecord,
    CrawlOutcome,
    CrawlRequest,
    DiscoveredLinkRecord,
    SourceProbeResult,
)


def _probe_result(probe: SourceProbe) -> SourceProbeResult:
    return SourceProbeResult(
        requested_url=str(probe.requested_url),
        final_url=str(probe.final_url) if probe.final_url else None,
        success=probe.success,
        http_status=probe.http_status,
        response_time_ms=probe.response_time_ms,
        content_type=probe.content_type,
        redirected=probe.redirected,
        robots_allowed=probe.robots_allowed,
        reason=probe.reason,
    )


def _link_record(link: DiscoveredLink) -> DiscoveredLinkRecord:
    return DiscoveredLinkRecord(
        url=str(link.url),
        title=link.title,
        discovered_via=link.discovered_via,
        published_at=link.published_at,
        topic_matches=tuple(link.topic_matches),
        discovery_methods=tuple(link.discovery_methods),
    )


def _page_record(page: CrawledPage) -> CrawledPageRecord:
    return CrawledPageRecord(
        url=str(page.url),
        final_url=str(page.final_url) if page.final_url else None,
        success=page.success,
        status_code=page.status_code,
        title=page.title,
        html=page.html,
        markdown=page.markdown,
        content_hash=page.content_hash,
        cache_status=page.cache_status,
        error=page.error,
        metadata=dict(page.metadata),
    )


class Crawl4AiSourceCrawler:
    """Delegate I/O to CrawlEngine and map its result to domain-owned records."""

    async def crawl(self, request: CrawlRequest) -> CrawlOutcome:
        policy = CrawlPolicy.model_validate(dict(request.policy))
        result = await CrawlEngine(policy).crawl(
            request.source_url,
            request.topic,
            request.date_from,
            request.date_to,
        )
        pages = tuple(_page_record(page) for page in result.pages)
        errors = tuple(
            message
            for message in (
                result.probe.reason if not result.probe.success else None,
                *(page.error for page in pages),
            )
            if message
        )
        return CrawlOutcome(
            source_url=str(result.source_url),
            probe=_probe_result(result.probe),
            discovered_links=tuple(_link_record(link) for link in result.discovered_links),
            pages=pages,
            started_at=result.started_at,
            finished_at=result.finished_at,
            errors=errors,
            diagnostics=result.diagnostics.model_dump(mode="json"),
        )

    async def probe(self, source_url: str) -> SourceProbeResult:
        return _probe_result(await CrawlEngine().probe(source_url))
