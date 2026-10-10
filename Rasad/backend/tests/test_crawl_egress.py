from unittest.mock import patch

import httpx
import pytest

from app.crawler.security import UnsafeUrlError
from app.modules.crawling.infrastructure.egress import (
    WORKER_RANGE_FALLBACK_BYTES,
    CrawlEgressPolicy,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/secret",
        "http://169.254.169.254/latest/meta-data",
        "http://user:password@example.com/",
        "http://service.internal/",
        "https://example.com:8443/",
    ],
)
async def test_trusted_proxy_rejects_unsafe_targets_before_network(url: str) -> None:
    policy = CrawlEgressPolicy("trusted_proxy", "http://crawler-egress:8080")
    with pytest.raises(UnsafeUrlError):
        await policy.validate(url)


def test_trusted_proxy_mode_fails_closed_without_proxy(monkeypatch) -> None:
    monkeypatch.setenv("CRAWL_EGRESS_MODE", "trusted_proxy")
    monkeypatch.delenv("CRAWL_PROXY_URL", raising=False)
    with pytest.raises(RuntimeError, match="requires a proxy"):
        CrawlEgressPolicy.from_environment()


@pytest.mark.asyncio
async def test_gateway_failure_does_not_fall_back_to_direct_network() -> None:
    class BrokenClient:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url: str):
            raise httpx.ConnectError("gateway unavailable")

    policy = CrawlEgressPolicy("trusted_proxy", "http://crawler-egress:8080")
    with patch(
        "app.modules.crawling.infrastructure.egress.httpx.AsyncClient",
        BrokenClient,
    ):
        with pytest.raises(UnsafeUrlError, match="egress is unavailable"):
            await policy.validate("https://www.bbc.com/news")


@pytest.mark.asyncio
async def test_gateway_rejection_is_sanitized() -> None:
    class RejectingClient:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url: str):
            return httpx.Response(403)

    secret_proxy = "http://proxy-user:do-not-leak@crawler-egress:8080"
    policy = CrawlEgressPolicy("trusted_proxy", secret_proxy)
    with patch(
        "app.modules.crawling.infrastructure.egress.httpx.AsyncClient",
        RejectingClient,
    ):
        with pytest.raises(UnsafeUrlError) as raised:
            await policy.validate("https://www.bbc.com/news")
    assert "do-not-leak" not in str(raised.value)


@pytest.mark.asyncio
async def test_worker_payload_forwards_only_allowlisted_headers() -> None:
    requests: list[dict] = []

    class RecordingClient:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url: str, **kwargs):
            requests.append(kwargs)
            return httpx.Response(
                200,
                headers={"X-Teb-Egress": "worker"},
                text="<html>ok</html>",
            )

    policy = CrawlEgressPolicy(
        mode="smart",
        worker_url="https://gateway.example",
        worker_secret="secret",
    )
    with patch(
        "app.modules.crawling.infrastructure.egress.httpx.AsyncClient",
        RecordingClient,
    ):
        await policy.fetch_through_worker(
            "https://www.bbc.com/news",
            headers={
                "Accept": "text/html",
                "Cookie": "must-not-leave-worker",
                "Authorization": "must-not-leave-worker",
            },
            timeout_seconds=10,
        )

    payload = requests[0]["content"].decode("utf-8")
    assert '"Accept":"text/html"' in payload
    assert "must-not-leave-worker" not in payload


@pytest.mark.asyncio
async def test_worker_413_retries_once_with_bounded_range() -> None:
    requests: list[dict] = []

    class RangeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url: str, **kwargs):
            requests.append(kwargs)
            if len(requests) == 1:
                return httpx.Response(
                    413,
                    headers={"X-Teb-Egress": "worker"},
                    text="Payload Too Large",
                )
            return httpx.Response(
                206,
                headers={
                    "X-Teb-Egress": "worker",
                    "Content-Type": "text/html",
                },
                text="<html>bounded</html>",
            )

    policy = CrawlEgressPolicy(
        mode="smart",
        worker_url="https://gateway.example",
        worker_secret="secret",
    )
    with patch(
        "app.modules.crawling.infrastructure.egress.httpx.AsyncClient",
        RangeClient,
    ):
        response = await policy.fetch_through_worker(
            "https://www.bbc.com/news",
            headers={"Accept": "text/html"},
            timeout_seconds=10,
        )

    assert len(requests) == 2
    expected_range = f'"Range":"bytes=0-{WORKER_RANGE_FALLBACK_BYTES - 1}"'
    assert expected_range in requests[1]["content"].decode("utf-8")
    assert response.status_code == 206
    assert response.headers["X-Rasad-Range-Fallback"] == "1"


@pytest.mark.asyncio
async def test_worker_413_without_worker_marker_is_not_retried() -> None:
    calls = 0

    class UntrustedClient:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url: str, **_kwargs):
            nonlocal calls
            calls += 1
            return httpx.Response(413, text="Payload Too Large")

    policy = CrawlEgressPolicy(
        mode="smart",
        worker_url="https://gateway.example",
        worker_secret="secret",
    )
    with patch(
        "app.modules.crawling.infrastructure.egress.httpx.AsyncClient",
        UntrustedClient,
    ):
        with pytest.raises(UnsafeUrlError, match="rejected"):
            await policy.fetch_through_worker(
                "https://www.bbc.com/news",
                headers={"Accept": "text/html"},
                timeout_seconds=10,
            )

    assert calls == 1
