from datetime import UTC, datetime
from math import ceil
from urllib.parse import urljoin
from uuid import UUID

import httpx

from app.core.config import settings
from app.crawler.security import validate_public_url
from app.modules.crawling.domain.source_health import connection_status
from app.modules.crawling.ports import SourceRepository
from app.models import Source
from app.models.enums import SourceStatus
from app.schemas.common import PageMeta, PaginatedResponse
from app.schemas.source import SourceCheckResponse, SourceCreate, SourceRead, SourceUpdate
from app.shared.domain.errors import ConflictError


ACCESS_RESTRICTED_STATUSES = {401, 403, 429}
def source_to_read(source: Source) -> SourceRead:
    return SourceRead(
        id=source.id,
        title=source.title,
        base_url=source.base_url,
        type=source.type,
        is_active=source.is_active,
        crawl_config=getattr(source, "crawl_config", None) or {},
        last_status=source.last_status,
        last_status_message=source.last_status_message,
        last_checked_at=source.last_checked_at,
        last_crawl_status=source.last_crawl_status,
        last_crawl_message=source.last_crawl_message,
        last_crawled_at=source.last_crawled_at,
    )


from app.modules.crawling.domain.errors import DuplicateSourceError


def _request_headers() -> dict[str, str]:
    return {
        "User-Agent": settings.crawler_user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "fa-IR,fa;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Upgrade-Insecure-Requests": "1",
    }


def _has_usable_html(response: httpx.Response) -> bool:
    content_type = response.headers.get("content-type", "").lower()
    text = response.text.strip()
    return "html" in content_type and "<html" in text[:500].lower() and len(text) >= 1000


def _reason_for_response(
    response: httpx.Response, success: bool, fallback_html: bool
) -> str | None:
    if fallback_html:
        return f"HTTP {response.status_code} دریافت شد، اما صفحه HTML قابل خزش است"
    if response.status_code in ACCESS_RESTRICTED_STATUSES:
        return (
            f"HTTP {response.status_code}: دسترسی مستقیم سایت محدود است، "
            "اما آدرس منبع معتبر است و در رصد از مسیرهای جایگزین مثل لینک‌ها، RSS یا sitemap بررسی می‌شود."
        )
    if success:
        return None
    if response.status_code in {500, 502, 503, 504}:
        return f"HTTP {response.status_code}: خطای موقت سمت سایت یا شبکه؛ منبع حذف یا غیرفعال نشده است."
    return f"HTTP {response.status_code}"


async def _probe_source(url: str) -> tuple[bool, int | None, int, str | None]:
    started_at = datetime.now(UTC)
    http_status: int | None = None
    reason: str | None = None
    success = False
    try:
        safe_url = await validate_public_url(url)
        async with httpx.AsyncClient(
            headers=_request_headers(),
            timeout=settings.crawler_page_timeout_ms / 1000,
            follow_redirects=True,
        ) as client:
            try:
                response = await client.get(safe_url)
            except httpx.TimeoutException as exc:
                response = await _probe_fallback_endpoint(client, safe_url)
                if response is None:
                    raise exc
        http_status = response.status_code
        fallback_html = response.status_code >= 400 and _has_usable_html(response)
        access_restricted = response.status_code in ACCESS_RESTRICTED_STATUSES
        success = response.status_code < 400 or fallback_html or access_restricted
        reason = _reason_for_response(response, success, fallback_html)
        if (
            success
            and str(response.url).rstrip("/") != safe_url.rstrip("/")
            and response.url.path.endswith("/robots.txt")
        ):
            reason = "صفحه اصلی کند یا محدود بود، اما robots.txt منبع با موفقیت پاسخ داد و دامنه معتبر است."
    except Exception as exc:
        reason = str(exc) or exc.__class__.__name__
    response_time_ms = round((datetime.now(UTC) - started_at).total_seconds() * 1000)
    if not success and not reason:
        reason = "بررسی منبع بدون پاسخ قابل تشخیص پایان یافت."
    if not success and reason and "timed out" in reason.casefold():
        reason = "Timeout: سایت در زمان تعیین‌شده پاسخ نداد؛ وضعیت قطعی منبع تغییر داده نشد."
    return success, http_status, response_time_ms, reason


async def _probe_fallback_endpoint(
    client: httpx.AsyncClient, safe_url: str
) -> httpx.Response | None:
    for path in ("/robots.txt", "/sitemap.xml", "/feed", "/rss"):
        try:
            response = await client.get(urljoin(safe_url, path), timeout=10)
        except httpx.HTTPError:
            continue
        if response.status_code < 400 or _has_usable_html(response):
            return response
    return None


class SourceQueryAdapter:
    def __init__(self, repository: SourceRepository) -> None:
        self._repository = repository

    async def list_sources(self, page: int, page_size: int, *, operational_only: bool = False) -> PaginatedResponse[SourceRead]:
        sources, total = await self._repository.list(page, page_size, operational_only=operational_only)
        return PaginatedResponse(
            items=[source_to_read(source) for source in sources],
            meta=PageMeta(
                page=page,
                page_size=page_size,
                total=total,
                pages=ceil(total / page_size) if total else 0,
            ),
        )


class SourceCommandAdapter:
    def __init__(self, repository: SourceRepository) -> None:
        self._repository = repository

    async def create_source(self, payload: SourceCreate, creator_id: UUID) -> SourceRead:
        source = Source(
            title=payload.title,
            base_url=str(payload.base_url),
            type=payload.type,
            is_active=payload.is_active,
            crawl_config=payload.crawl_config.storage_dict(),
            created_by=creator_id,
        )
        try:
            await self._repository.add(source)
        except ConflictError as exc:
            raise DuplicateSourceError(str(exc)) from exc
        return source_to_read(source)

    async def update_source(self, source_id: UUID, payload: SourceUpdate) -> SourceRead | None:
        source = await self._repository.get(source_id)
        if source is None:
            return None
        source.title = payload.title
        source.base_url = str(payload.base_url)
        source.type = payload.type
        source.is_active = payload.is_active
        source.crawl_config = payload.crawl_config.storage_dict()
        await self._safe_flush()
        return source_to_read(source)

    async def delete_source(self, source_id: UUID) -> bool:
        source = await self._repository.get(source_id)
        if source is None:
            return False
        await self._repository.delete(source)
        return True

    async def check_source(self, source_id: UUID) -> SourceCheckResponse | None:
        source = await self._repository.get(source_id)
        if source is None:
            return None
        checked_at = datetime.now(UTC)
        success, http_status, response_time_ms, reason = await _probe_source(source.base_url)

        source.last_status = SourceStatus(connection_status(success, http_status, reason))
        source.last_status_message = reason
        source.last_http_status = http_status
        source.last_response_time_ms = response_time_ms
        source.last_checked_at = checked_at
        await self._safe_flush()
        return SourceCheckResponse(
            status=source.last_status,
            http_status=http_status,
            response_time_ms=response_time_ms,
            reason=reason,
            last_checked_at=checked_at,
        )

    async def _safe_flush(self) -> None:
        try:
            await self._repository.flush()
        except ConflictError as exc:
            raise DuplicateSourceError(str(exc)) from exc
