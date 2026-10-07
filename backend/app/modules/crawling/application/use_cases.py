"""Single-purpose crawling application use cases."""

from datetime import date
from typing import Mapping

from app.modules.crawling.domain import CrawlOutcome, CrawlRequest, SourceProbeResult
from app.modules.crawling.ports import SourceCrawler


class ProbeSource:
    def __init__(self, crawler: SourceCrawler) -> None:
        self._crawler = crawler

    async def execute(self, source_url: str) -> SourceProbeResult:
        return await self._crawler.probe(source_url)


class BuildCrawlRequest:
    """Create the framework-independent crawl command from source configuration."""

    def execute(
        self,
        *,
        source_url: str,
        topic: str,
        date_from: date | None,
        date_to: date | None,
        policy: Mapping[str, object] | None = None,
    ) -> CrawlRequest:
        return CrawlRequest(
            source_url=source_url,
            topic=topic,
            date_from=date_from,
            date_to=date_to,
            policy=dict(policy or {}),
        )


class CrawlSource:
    def __init__(self, crawler: SourceCrawler) -> None:
        self._crawler = crawler

    async def execute(self, request: CrawlRequest) -> CrawlOutcome:
        return await self._crawler.crawl(request)
