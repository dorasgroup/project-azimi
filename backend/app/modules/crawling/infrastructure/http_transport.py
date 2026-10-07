"""Safe HTTP transport helpers used by crawler infrastructure."""

import gzip
from urllib.parse import urljoin

import httpx

from app.modules.crawling.infrastructure.egress import CrawlEgressPolicy


ACCESS_RESTRICTED_STATUSES = {401, 403, 429}
WORKER_RETRY_STATUSES = {401, 403, 429, 451}


def request_headers(user_agent: str) -> dict[str, str]:
    return {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "fa-IR,fa;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Upgrade-Insecure-Requests": "1",
    }


def has_usable_html(
    status_code: int | None,
    content_type: str | None,
    html: str | None,
) -> bool:
    if status_code is None or status_code < 400:
        return False
    body = (html or "").strip()
    looks_like_html = "<html" in body[:500].lower() and len(body) >= 1000
    return looks_like_html and (not content_type or "html" in content_type.lower())


def access_restricted_reason(status_code: int) -> str:
    return (
        f"HTTP {status_code}: دسترسی مستقیم سایت محدود است، "
        "اما آدرس منبع معتبر است و در رصد از مسیرهای جایگزین مانند لینک‌ها، "
        "RSS یا sitemap بررسی می‌شود."
    )


def response_text(response: httpx.Response) -> str:
    content_type = response.headers.get("content-type", "").lower()
    if str(response.url).lower().endswith(".gz") or "gzip" in content_type:
        try:
            return gzip.decompress(response.content).decode(
                response.encoding or "utf-8", errors="replace"
            )
        except OSError:
            return response.text
    return response.text


class SafeHttpTransport:
    """Perform validated HTTP GET requests and follow only validated redirects."""

    def __init__(
        self,
        *,
        user_agent: str,
        timeout_ms: int,
        egress: CrawlEgressPolicy | None = None,
    ) -> None:
        self._user_agent = user_agent
        self._timeout_seconds = timeout_ms / 1000
        self._egress = egress or CrawlEgressPolicy()
        self._client = None
        self._worker_client = None

    def _shared_client(self):
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(headers=request_headers(self._user_agent),
                timeout=self._timeout_seconds, follow_redirects=False,
                proxy=self._egress.proxy_url, trust_env=False,
                limits=httpx.Limits(max_connections=12, max_keepalive_connections=12))
        return self._client

    def _shared_worker_client(self):
        if self._worker_client is None or self._worker_client.is_closed:
            # Production POC: reusing the empty HEAD-via-POST response's TCP
            # connection stalls the following GET-via-POST. Keep the bounded
            # client pool, but never reuse a Worker connection. Origin request
            # headers/proxies must not implicitly become Worker gateway headers.
            self._worker_client = httpx.AsyncClient(
                timeout=max(self._timeout_seconds, self._egress.control_timeout_seconds),
                follow_redirects=False, trust_env=False,
                limits=httpx.Limits(max_connections=12, max_keepalive_connections=0),
            )
        return self._worker_client

    async def _close(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        if self._worker_client is not None:
            await self._worker_client.aclose()
            self._worker_client = None

    async def probe(self, url: str) -> httpx.Response:
        """Use HEAD only for Worker reachability; content retrieval stays GET.

        The publisher and every redirect still pass the egress policy. A legacy
        Worker may ignore HEAD and return GET; unsupported HEAD falls back once.
        Robots decisions remain mandatory in SourceProbeService/page fetching.
        """
        current = await self._egress.validate(url)
        if not self._egress.should_use_worker(current):
            return await self.get(current)
        response = await self._egress.fetch_through_worker(
            current,
            headers=request_headers(self._user_agent),
            timeout_seconds=self._timeout_seconds,
            method="HEAD",
            client=self._shared_worker_client(),
        )
        if response.status_code in {405, 501}:
            return await self.get(current)
        return response

    async def get(self, url: str, *, max_redirects: int = 5) -> httpx.Response:
        current = await self._egress.validate(url)
        headers = request_headers(self._user_agent)
        for _ in range(max_redirects + 1):
            if self._egress.should_use_worker(current):
                response = await self._egress.fetch_through_worker(
                    current,
                    headers=headers,
                    timeout_seconds=self._timeout_seconds,
                    client=self._shared_worker_client(),
                )
            else:
                try:
                    response = await self._shared_client().get(current)
                except (httpx.TimeoutException, httpx.TransportError):
                    if not self._egress.can_retry_through_worker(current):
                        raise
                    response = await self._egress.fetch_through_worker(
                        current,
                        headers=headers,
                        timeout_seconds=self._timeout_seconds,
                        client=self._shared_worker_client(),
                    )
                if (
                    response.status_code in WORKER_RETRY_STATUSES
                    and self._egress.can_retry_through_worker(current)
                ):
                    response = await self._egress.fetch_through_worker(
                        current,
                        headers=headers,
                        timeout_seconds=self._timeout_seconds,
                        client=self._shared_worker_client(),
                    )
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    return response
                current = urljoin(current, location)
                await self._egress.validate(current)
                continue
            return response
        raise ValueError("Too many redirects")
