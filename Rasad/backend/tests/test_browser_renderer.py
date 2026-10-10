from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytest.importorskip("crawl4ai")

from app.crawler.schemas import CrawlPolicy
from app.modules.crawling.infrastructure.browser_renderer import BrowserPageRenderer


SOURCE_URL = "https://news.example.com"
PAGE_URL = f"{SOURCE_URL}/story"


async def _valid_url(url: str) -> str:
    return url


class _AllowAllEgress:
    proxy_url = None

    async def validate(self, url: str) -> str:
        return url


async def _robots_allowed(_url: str) -> bool:
    return True


@pytest.mark.asyncio
async def test_browser_renderer_bypasses_cache_and_rejects_rendered_shell() -> None:
    captured_config = None

    class FakeCrawler:
        async def arun(self, *, url: str, config):
            nonlocal captured_config
            captured_config = config
            return SimpleNamespace(
                redirected_url=None,
                url=url,
                html="<html><body>loading<script>app()</script></body></html>",
                metadata={"content-type": "text/html"},
                status_code=200,
                success=True,
                markdown=SimpleNamespace(raw_markdown="loading"),
                cache_status=None,
                error_message=None,
            )

    renderer = BrowserPageRenderer(CrawlPolicy(), "test-agent", _AllowAllEgress())
    with (
        patch(
            "app.modules.crawling.infrastructure.browser_renderer.host_is_allowed",
            return_value=True,
        ),
    ):
        page = await renderer.render(
            FakeCrawler(), SOURCE_URL, PAGE_URL, _robots_allowed, "shell"
        )

    assert captured_config.cache_mode.value == "bypass"
    assert page.success is False
    assert page.error == "Browser rendering did not produce enough visible text"


@pytest.mark.asyncio
async def test_browser_error_response_is_not_news_even_with_large_html():
    class FakeCrawler:
        async def arun(self, *, url, config):
            return SimpleNamespace(redirected_url=None, url=url, html='<html>'+'Access denied '*300+'</html>',
                metadata={}, status_code=403, success=True, markdown=None, cache_status=None, error_message=None)
    renderer = BrowserPageRenderer(CrawlPolicy(), 'test', _AllowAllEgress())
    page = await renderer.render(FakeCrawler(), SOURCE_URL, PAGE_URL, _robots_allowed, 'shell')
    assert not page.success and page.metadata['failure_kind'] == 'access_restricted'
