from types import SimpleNamespace
from uuid import uuid4

from app.models.enums import SourceCrawlStatus, SourceStatus, SourceType
from app.modules.crawling.domain.source_health import connection_status
from app.modules.crawling.infrastructure.source_operations import source_to_read


def test_connection_status_does_not_depend_on_crawl_yield() -> None:
    assert connection_status(True, 200, None) == "success"
    assert connection_status(False, 503, "temporary outage") == "unknown"
    assert connection_status(False, 404, "HTTP 404") == "failed"
    assert connection_status(False, None, "Source probe time budget exhausted") == "unknown"


def test_source_read_exposes_connection_and_crawl_status_separately() -> None:
    source = SimpleNamespace(
        id=uuid4(),
        title="Example",
        base_url="https://example.com",
        type=SourceType.NEWS_SITE,
        is_active=True,
        last_status=SourceStatus.SUCCESS,
        last_status_message=None,
        last_checked_at=None,
        last_crawl_status=SourceCrawlStatus.EMPTY,
        last_crawl_message="هیچ خبر مرتبطی در بازه پیدا نشد.",
        last_crawled_at=None,
    )

    result = source_to_read(source)

    assert result.is_active is True
    assert result.last_status == SourceStatus.SUCCESS
    assert result.last_crawl_status == SourceCrawlStatus.EMPTY
