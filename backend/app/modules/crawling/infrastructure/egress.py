"""Explicit, fail-closed egress policy for crawler network traffic."""

from __future__ import annotations

import ipaddress
import json
import logging
import os
from contextlib import nullcontext
from dataclasses import dataclass, field
from urllib.parse import quote, urlparse

import httpx

from app.crawler.security import UnsafeUrlError, validate_public_url


DEFAULT_DIRECT_HOSTS = frozenset({"tasnimnews.com", "sharghdaily.com"})
WORKER_REQUEST_MAX_BYTES = 64 * 1024
WORKER_RANGE_FALLBACK_BYTES = 1024 * 1024
WORKER_FORWARDED_HEADERS = frozenset(
    {
        "accept",
        "accept-language",
        "cache-control",
        "pragma",
        "range",
        "upgrade-insecure-requests",
        "user-agent",
    }
)
logger = logging.getLogger(__name__)
DEFAULT_WORKER_HOSTS = frozenset(
    {
        "aljazeera.net",
        "bbc.com",
        "dw.com",
        "economist.com",
        "euronews.com",
        "foreignaffairs.com",
        "ft.com",
        "haaretz.com",
        "iranintl.com",
        "lemonde.fr",
        "nytimes.com",
        "politico.eu",
        "radiofarda.com",
        "theatlantic.com",
        "theguardian.com",
        "time.com",
        "voanews.com",
        "washingtonpost.com",
        "wsj.com",
    }
)


def _host_set(value: str, defaults: frozenset[str]) -> frozenset[str]:
    configured = {item.strip().casefold().rstrip(".") for item in value.split(",") if item.strip()}
    return frozenset(configured or defaults)


def _host_matches(host: str, candidates: frozenset[str]) -> bool:
    return any(host == candidate or host.endswith(f".{candidate}") for candidate in candidates)


def _public_literal(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return True
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _worker_payload(
    url: str,
    headers: dict[str, str],
    *,
    byte_range: str | None = None,
    method: str = "GET",
) -> bytes:
    forwarded = {
        name: value
        for name, value in headers.items()
        if name.casefold() in WORKER_FORWARDED_HEADERS
    }
    if byte_range:
        forwarded = {
            name: value
            for name, value in forwarded.items()
            if name.casefold() != "range"
        }
        forwarded["Range"] = byte_range
    data = {"url": url, "headers": forwarded}
    if method == "HEAD":
        data["method"] = "HEAD"
    payload = json.dumps(
        data,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(payload) > WORKER_REQUEST_MAX_BYTES:
        raise UnsafeUrlError("Crawler Worker request metadata is too large")
    return payload


def _validate_target_shape(url: str) -> tuple[str, int]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise UnsafeUrlError("Only public HTTP and HTTPS URLs are allowed")
    if parsed.username or parsed.password:
        raise UnsafeUrlError("Credentials in URLs are not allowed")
    host = parsed.hostname.rstrip(".").casefold()
    if (
        host == "localhost"
        or host.endswith((".localhost", ".local", ".internal"))
        or not _public_literal(host)
    ):
        raise UnsafeUrlError("Local or non-public targets are not allowed")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise UnsafeUrlError("Invalid target port") from exc
    if port not in {80, 443}:
        raise UnsafeUrlError("Only HTTP and HTTPS target ports are allowed")
    return host, port


@dataclass(frozen=True)
class CrawlEgressPolicy:
    """Choose direct, trusted-proxy, or smart Cloudflare Worker egress."""

    mode: str = "direct"
    proxy_url: str | None = None
    control_timeout_seconds: float = 12.0
    worker_url: str | None = None
    worker_secret: str | None = None
    direct_hosts: frozenset[str] = DEFAULT_DIRECT_HOSTS
    worker_hosts: frozenset[str] = DEFAULT_WORKER_HOSTS
    _admission: object | None = field(default=None, repr=False, compare=False)

    @classmethod
    def from_environment(cls) -> "CrawlEgressPolicy":
        mode = os.getenv("CRAWL_EGRESS_MODE", "direct").strip().casefold()
        proxy_url = os.getenv("CRAWL_PROXY_URL", "").strip() or None
        worker_url = (
            os.getenv("CRAWL_WORKER_URL", "").strip()
            or os.getenv("AI_CLOUDFLARE_WORKER_URL", "").strip()
            or None
        )
        worker_secret = (
            os.getenv("CRAWL_WORKER_SECRET", "").strip()
            or os.getenv("AI_CLOUDFLARE_WORKER_SECRET", "").strip()
            or None
        )
        if mode not in {"direct", "trusted_proxy", "smart"}:
            raise RuntimeError("Unsupported crawler egress mode")
        if mode == "trusted_proxy" and not proxy_url:
            raise RuntimeError("Trusted crawler egress requires a proxy")
        if proxy_url:
            parsed = urlparse(proxy_url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise RuntimeError("Crawler proxy URL is invalid")
        if mode == "smart":
            if not worker_url or not worker_secret:
                raise RuntimeError("Smart crawler egress requires the Cloudflare Worker")
            parsed = urlparse(worker_url)
            if parsed.scheme != "https" or not parsed.hostname:
                raise RuntimeError("Crawler Worker URL is invalid")
        admission = None
        if mode == 'smart' and worker_url:
            from app.core.config import settings
            from app.modules.crawling.infrastructure.worker_admission import WorkerAdmission
            admission = WorkerAdmission(settings.redis_url, worker_url)
        return cls(
            mode=mode,
            proxy_url=proxy_url,
            worker_url=worker_url.rstrip("/") if worker_url else None,
            worker_secret=worker_secret,
            direct_hosts=_host_set(os.getenv("CRAWL_DIRECT_HOSTS", ""), DEFAULT_DIRECT_HOSTS),
            worker_hosts=_host_set(os.getenv("CRAWL_WORKER_HOSTS", ""), DEFAULT_WORKER_HOSTS),
            _admission=admission,
        )

    def route(self, url: str) -> str:
        host, _ = _validate_target_shape(url)
        if host.endswith(".ir") or _host_matches(host, self.direct_hosts):
            return "direct"
        if _host_matches(host, self.worker_hosts):
            return "worker"
        return "auto"

    def should_use_worker(self, url: str) -> bool:
        return self.mode == "smart" and self.route(url) == "worker"

    def can_retry_through_worker(self, url: str) -> bool:
        return self.mode == "smart" and self.route(url) != "direct"

    async def validate(self, url: str) -> str:
        if self.mode == "direct":
            return await validate_public_url(url)

        if self.mode == "smart":
            if self.should_use_worker(url):
                _validate_target_shape(url)
                return url
            return await validate_public_url(url)

        host, port = _validate_target_shape(url)
        if not self.proxy_url:
            raise UnsafeUrlError("Crawler egress is unavailable")
        control_url = (
            f"{self.proxy_url.rstrip('/')}/__egress__/resolve"
            f"?host={quote(host, safe='')}&port={port}"
        )
        try:
            async with httpx.AsyncClient(
                timeout=self.control_timeout_seconds,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = await client.get(control_url)
            if response.status_code != 204:
                raise UnsafeUrlError("Crawler destination was rejected by egress policy")
        except UnsafeUrlError:
            raise
        except (httpx.HTTPError, OSError) as exc:
            raise UnsafeUrlError("Crawler egress is unavailable") from exc
        return url

    async def fetch_through_worker(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout_seconds: float,
        method: str = "GET",
        client: httpx.AsyncClient | None = None,
    ) -> httpx.Response:
        if method not in {"GET", "HEAD"}:
            raise UnsafeUrlError("Unsupported crawler Worker method")
        _validate_target_shape(url)
        if self.mode != "smart" or not self.worker_url or not self.worker_secret:
            raise UnsafeUrlError("Crawler Worker is unavailable")
        request_payload = _worker_payload(url, headers, method=method)
        range_fallback_used = False
        try:
            async with (self._admission.slot() if self._admission else nullcontext()), (nullcontext(client) if client is not None else httpx.AsyncClient(
                timeout=max(timeout_seconds, self.control_timeout_seconds),
                follow_redirects=False,
                trust_env=False,
            )) as client:
                response = await client.post(
                    f"{self.worker_url}/web/fetch",
                    headers={
                        "Content-Type": "application/json",
                        "X-Teb-AI-Gateway-Secret": self.worker_secret,
                    },
                    content=request_payload,
                )
                if (
                    response.status_code == 413
                    and method == "GET"
                    and response.headers.get("X-Teb-Egress") == "worker"
                ):
                    range_fallback_used = True
                    range_value = f"bytes=0-{WORKER_RANGE_FALLBACK_BYTES - 1}"
                    retry_payload = _worker_payload(
                        url,
                        headers,
                        byte_range=range_value,
                    )
                    logger.warning(
                        "Crawler Worker response exceeded its body limit; retrying with a bounded range",
                        extra={
                            "target_host": urlparse(url).hostname,
                            "request_bytes": len(request_payload),
                            "range_bytes": WORKER_RANGE_FALLBACK_BYTES,
                        },
                    )
                    response = await client.post(
                        f"{self.worker_url}/web/fetch",
                        headers={
                            "Content-Type": "application/json",
                            "X-Teb-AI-Gateway-Secret": self.worker_secret,
                        },
                        content=retry_payload,
                    )
        except (httpx.HTTPError, OSError, TimeoutError) as exc:
            # This is retryable network transport, not a rejected URL. Keep the
            # original body/headers/credentials out of user-facing diagnostics.
            raise httpx.TransportError("Crawler Worker is unavailable") from exc
        if response.headers.get("X-Teb-Egress") != "worker":
            if response.status_code == 429 and response.headers.get('X-Rasad-Worker-Error') == 'rate_limited':
                return httpx.Response(429, headers={'Retry-After': response.headers.get('Retry-After', '10'),
                    'X-Rasad-Worker-Error': 'rate_limited'}, request=httpx.Request('GET', url))
            raise UnsafeUrlError("Crawler Worker rejected the request")
        final_url = response.headers.get("X-Teb-Final-Url") or url
        _validate_target_shape(final_url)
        safe_headers = {
            name: value
            for name, value in response.headers.items()
            if name.casefold()
            not in {
                "content-encoding",
                "content-length",
                "x-teb-final-url",
                "x-teb-egress",
            }
        }
        if range_fallback_used:
            safe_headers["X-Rasad-Range-Fallback"] = "1"
        return httpx.Response(
            response.status_code,
            headers=safe_headers,
            content=response.content,
            request=httpx.Request("GET", final_url),
        )
