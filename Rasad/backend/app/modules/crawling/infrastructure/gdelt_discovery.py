"""Bounded historical URL discovery through the GDELT DOC API."""

import logging
from datetime import UTC, date, datetime, time
from math import ceil
from urllib.parse import urlencode, urlparse

from app.crawler.discovery import canonicalize_url, link_matches_topic
from app.crawler.schemas import CrawlPolicy, DiscoveredLink
from app.crawler.security import host_is_allowed
from app.modules.crawling.domain.historical_retrieval import balanced_date_windows
from app.modules.crawling.infrastructure.source_probe import HttpGet
from app.processing.query_planner import query_plan


GDELT_DOC_ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"
logger = logging.getLogger(__name__)


def _gdelt_datetime(value: date, *, end: bool) -> str:
    instant = datetime.combine(value, time.max if end else time.min, tzinfo=UTC)
    return instant.strftime("%Y%m%d%H%M%S")


def _seen_date(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("T", "").replace("Z", "")
    try:
        return datetime.strptime(normalized, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            return None


def _gdelt_query(topic: str, host: str, languages: tuple[str, ...] | None) -> str:
    # GDELT DOC searches its English/translingual index; always include the
    # controlled English retrieval variants even when the source itself is Persian.
    retrieval_languages = tuple(dict.fromkeys((*languages, "en"))) if languages else ("en",)
    queries = query_plan(topic, retrieval_languages).queries[:4]
    quoted = [f'"{query.replace(chr(34), "")}"' for query in queries]
    expression = quoted[0] if len(quoted) == 1 else f"({' OR '.join(quoted)})"
    return f"{expression} domain:{host}"


class GdeltDiscoveryProvider:
    """Query GDELT for topic-matching URLs belonging to one source host."""

    def __init__(
        self,
        policy: CrawlPolicy,
        languages: tuple[str, ...] | None = None,
    ) -> None:
        self._policy = policy
        self._languages = languages

    async def discover(
        self,
        source_url: str,
        topic: str,
        fetch: HttpGet,
        date_from: date,
        date_to: date,
    ) -> list[DiscoveredLink]:
        host = urlparse(source_url).hostname or ""
        query = _gdelt_query(topic, host, self._languages)
        windows = balanced_date_windows(date_from, date_to)
        if not windows:
            return []
        per_window_limit = min(
            250,
            max(1, ceil(self._policy.max_external_results / len(windows))),
        )
        links: list[DiscoveredLink] = []
        seen: set[str] = set()
        successful_windows = 0
        failed_windows = 0
        for window_from, window_to in windows:
            params = {
                "query": query,
                "mode": "artlist",
                "format": "json",
                "sort": "datedesc",
                "maxrecords": per_window_limit,
                "startdatetime": _gdelt_datetime(window_from, end=False),
                "enddatetime": _gdelt_datetime(window_to, end=True),
            }
            try:
                response = await fetch(f"{GDELT_DOC_ENDPOINT}?{urlencode(params)}")
                if response.status_code >= 400:
                    failed_windows += 1
                    continue
                payload = response.json()
            except Exception:
                failed_windows += 1
                continue
            successful_windows += 1
            articles = payload.get("articles", []) if isinstance(payload, dict) else []
            for article in articles:
                if not isinstance(article, dict):
                    continue
                raw_url = article.get("url")
                if not isinstance(raw_url, str) or not host_is_allowed(
                    raw_url,
                    source_url,
                    self._policy.allow_subdomains,
                ):
                    continue
                url = canonicalize_url(raw_url)
                if url in seen:
                    continue
                observed_at = _seen_date(article.get("seendate"))
                if observed_at and not (
                    window_from <= observed_at.date() <= window_to
                ):
                    continue
                title = (
                    article.get("title")
                    if isinstance(article.get("title"), str)
                    else None
                )
                links.append(
                    DiscoveredLink(
                        url=url,
                        title=title,
                        discovered_via="gdelt",
                        # GDELT's seen date bounds discovery but is not asserted as
                        # the publisher's date; phase three resolves that from HTML.
                        published_at=None,
                        topic_matches=link_matches_topic(url, title, topic),
                        discovery_methods=["gdelt"],
                    )
                )
                seen.add(url)
                if len(links) >= self._policy.max_external_results:
                    break
            if len(links) >= self._policy.max_external_results:
                break
        logger.info(
            "crawl_gdelt_historical_discovery",
            extra={
                "historical_trace": {
                    "windows_planned": len(windows),
                    "windows_succeeded": successful_windows,
                    "windows_failed": failed_windows,
                    "result_count": len(links),
                    "date_from": date_from.isoformat(),
                    "date_to": date_to.isoformat(),
                }
            },
        )
        return links
