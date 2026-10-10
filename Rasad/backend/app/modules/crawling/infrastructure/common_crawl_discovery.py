"""Bounded historical URL discovery through Common Crawl CDX indexes."""

import json
import logging
from datetime import UTC, date, datetime
from math import ceil
from urllib.parse import urlencode, urlparse

from app.crawler.discovery import (
    canonicalize_url,
    is_probable_content_url,
    link_matches_topic,
)
from app.crawler.schemas import CrawlPolicy, DiscoveredLink
from app.crawler.security import host_is_allowed
from app.modules.crawling.domain.historical_retrieval import (
    DEFAULT_HISTORICAL_RETRIEVAL_BUDGET,
    evenly_spaced_pages,
)
from app.modules.crawling.infrastructure.source_probe import HttpGet


COMMON_CRAWL_COLLECTIONS = "https://index.commoncrawl.org/collinfo.json"
logger = logging.getLogger(__name__)


def _collection_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _capture_date(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    except ValueError:
        return None


class CommonCrawlDiscoveryProvider:
    """Find captured article URLs in crawl collections overlapping a date range."""

    def __init__(self, policy: CrawlPolicy) -> None:
        self._policy = policy

    async def _collections(
        self,
        fetch: HttpGet,
        date_from: date,
        date_to: date,
    ) -> list[str]:
        try:
            response = await fetch(COMMON_CRAWL_COLLECTIONS)
            if response.status_code >= 400:
                return []
            payload = response.json()
        except Exception:
            return []
        matches: list[tuple[date, str]] = []
        for item in payload if isinstance(payload, list) else []:
            if not isinstance(item, dict) or not isinstance(item.get("cdx-api"), str):
                continue
            start = _collection_date(item.get("from"))
            end = _collection_date(item.get("to"))
            if start and end and start <= date_to and end >= date_from:
                matches.append((end, item["cdx-api"]))
        matches.sort(reverse=True)
        return [url for _, url in matches[: self._policy.common_crawl_max_indexes]]

    async def discover(
        self,
        source_url: str,
        topic: str,
        fetch: HttpGet,
        date_from: date,
        date_to: date,
    ) -> list[DiscoveredLink]:
        collection_urls = await self._collections(fetch, date_from, date_to)
        if not collection_urls:
            return []
        host = urlparse(source_url).hostname or ""
        links: dict[str, DiscoveredLink] = {}
        max_page_slots = (
            len(collection_urls)
            * DEFAULT_HISTORICAL_RETRIEVAL_BUDGET.common_crawl_max_pages_per_index
        )
        per_query_limit = max(
            10,
            ceil(self._policy.max_external_results / max(1, max_page_slots)),
        )
        pages_attempted = 0
        pages_succeeded = 0
        page_count_failures = 0
        for collection_url in collection_urls:
            base_params = [
                ("url", f"{host}/*"),
                ("output", "json"),
                ("filter", "status:200"),
                ("filter", "mime:text/html"),
                ("from", date_from.strftime("%Y%m%d")),
                ("to", date_to.strftime("%Y%m%d")),
                ("limit", str(per_query_limit)),
            ]
            try:
                count_params = [
                    *base_params,
                    ("showNumPages", "true"),
                    (
                        "pageSize",
                        str(
                            DEFAULT_HISTORICAL_RETRIEVAL_BUDGET.common_crawl_page_size
                        ),
                    ),
                ]
                count_response = await fetch(
                    f"{collection_url}?{urlencode(count_params)}"
                )
                count_payload = (
                    count_response.json() if count_response.status_code < 400 else {}
                )
                total_pages = (
                    int(count_payload.get("pages", 1))
                    if isinstance(count_payload, dict)
                    else 1
                )
            except Exception:
                total_pages = 1
                page_count_failures += 1
            for page in evenly_spaced_pages(total_pages):
                pages_attempted += 1
                params = [
                    *base_params,
                    ("page", str(page)),
                    (
                        "pageSize",
                        str(
                            DEFAULT_HISTORICAL_RETRIEVAL_BUDGET.common_crawl_page_size
                        ),
                    ),
                ]
                try:
                    response = await fetch(f"{collection_url}?{urlencode(params)}")
                    if response.status_code >= 400:
                        continue
                except Exception:
                    continue
                pages_succeeded += 1
                for line in response.text.splitlines():
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    raw_url = record.get("url") if isinstance(record, dict) else None
                    if not isinstance(raw_url, str) or not host_is_allowed(
                        raw_url,
                        source_url,
                        self._policy.allow_subdomains,
                    ):
                        continue
                    url = canonicalize_url(raw_url)
                    if not is_probable_content_url(url):
                        continue
                    captured_at = _capture_date(record.get("timestamp"))
                    if captured_at and not (
                        date_from <= captured_at.date() <= date_to
                    ):
                        continue
                    links[url] = DiscoveredLink(
                        url=url,
                        discovered_via="common_crawl",
                        published_at=None,
                        topic_matches=link_matches_topic(url, None, topic),
                        discovery_methods=["common_crawl"],
                    )
                    if len(links) >= self._policy.max_external_results:
                        break
                if len(links) >= self._policy.max_external_results:
                    break
            if len(links) >= self._policy.max_external_results:
                break
        logger.info(
            "crawl_common_crawl_historical_discovery",
            extra={
                "historical_trace": {
                    "collections": len(collection_urls),
                    "pages_attempted": pages_attempted,
                    "pages_succeeded": pages_succeeded,
                    "page_count_failures": page_count_failures,
                    "result_count": len(links),
                    "date_from": date_from.isoformat(),
                    "date_to": date_to.isoformat(),
                }
            },
        )
        return list(links.values())
