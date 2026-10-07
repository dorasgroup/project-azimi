"""Map crawl diagnostics to the persisted source health fields."""

from app.crawler.schemas import CrawlBatchResult
from app.models import Source
from app.models.enums import SourceCrawlStatus, SourceStatus
from app.modules.crawling.domain.source_health import connection_status


class SourceCrawlHealthUpdater:
    """Update only source-health fields from one completed crawl result."""

    @staticmethod
    def update(source: Source, crawl_result: CrawlBatchResult) -> None:
        source.last_status = SourceStatus(
            connection_status(
                crawl_result.probe.success,
                crawl_result.probe.http_status,
                crawl_result.probe.reason,
            )
        )
        source.last_status_message = crawl_result.probe.reason
        source.last_http_status = crawl_result.probe.http_status
        source.last_response_time_ms = crawl_result.probe.response_time_ms
        source.last_checked_at = crawl_result.finished_at
        try:
            source.last_crawl_status = SourceCrawlStatus(crawl_result.diagnostics.status)
        except ValueError:
            source.last_crawl_status = SourceCrawlStatus.UNKNOWN
        source.last_crawl_message = crawl_result.diagnostics.summary
        source.last_crawled_at = crawl_result.finished_at
