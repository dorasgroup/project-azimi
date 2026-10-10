import httpx
import pytest

from app.modules.crawling.infrastructure.source_capability_detector import SourceCapabilityDetector


@pytest.mark.asyncio
async def test_detector_records_observed_and_declared_capabilities() -> None:
    async def fetch(url: str, **_kwargs) -> httpx.Response:
        if url.endswith("robots.txt"):
            body, content_type = "Sitemap: /sitemap.xml", "text/plain"
        elif url.endswith(("feed", "rss")):
            body, content_type = "<rss />", "application/rss+xml"
        elif url.endswith("sitemap_index.xml"):
            body, content_type = "<sitemapindex />", "application/xml"
        else:
            body, content_type = "<html />", "text/html"
        return httpx.Response(
            200,
            text=body,
            headers={"content-type": content_type},
            request=httpx.Request("GET", url),
        )

    snapshot = await SourceCapabilityDetector().detect("https://www.irna.ir", fetch)

    assert snapshot.profile_key == "irna"
    assert snapshot.http_reachable is True
    assert snapshot.robots_reachable is True
    assert snapshot.feed_reachable is True
    assert snapshot.sitemap_reachable is True
    assert snapshot.remote_egress_required is False


@pytest.mark.asyncio
async def test_detector_preserves_network_failure_evidence() -> None:
    async def fetch(url: str, **_kwargs) -> httpx.Response:
        raise httpx.ConnectError("blocked", request=httpx.Request("GET", url))

    snapshot = await SourceCapabilityDetector().detect("https://www.theguardian.com", fetch)

    assert snapshot.http_reachable is False
    assert snapshot.remote_egress_required is True
    assert snapshot.evidence["base"].startswith("ConnectError:")
