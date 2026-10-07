"""robots.txt sitemap seeding and bounded recursive sitemap traversal."""

import asyncio
import re
from collections import deque
from datetime import UTC, date, datetime
from urllib.parse import urljoin, urlparse

from app.crawler.discovery import (
    SitemapReference,
    canonicalize_url,
    discover_from_xml,
    discover_sitemap_references,
    discover_sitemaps_from_robots,
)
from app.crawler.schemas import CrawlPolicy, DiscoveredLink
from app.modules.crawling.infrastructure.http_transport import response_text
from app.modules.crawling.infrastructure.source_probe import HttpGet
from app.processing.dates import gregorian_to_jalali


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class SitemapDiscoveryProvider:
    """Discover sitemap seeds and recursively traverse nested sitemap indexes."""

    def __init__(self, policy: CrawlPolicy) -> None:
        self._policy = policy

    async def robots_sitemaps(self, source_url: str, fetch: HttpGet) -> list[str]:
        robots_url = canonicalize_url(urljoin(source_url, "/robots.txt"))
        try:
            response = await fetch(robots_url)
            if response.status_code >= 400:
                return []
            return discover_sitemaps_from_robots(
                response.text,
                source_url,
                self._policy.allow_subdomains,
            )
        except Exception:
            return []

    @staticmethod
    def prioritize_references(
        references: list[SitemapReference],
        *,
        date_from: date | None,
        date_to: date | None,
    ) -> list[SitemapReference]:
        target_years = (
            {str(year) for year in range(date_from.year, date_to.year + 1)}
            if date_from and date_to
            else set()
        )
        target_months: set[str] = set()
        if date_from and date_to:
            current = date(date_from.year, date_from.month, 1)
            final = date(date_to.year, date_to.month, 1)
            while current <= final:
                target_months.update(
                    {
                        f"{current.year}-{current.month:02d}",
                        f"{current.year}/{current.month:02d}",
                        f"{current.year}{current.month:02d}",
                    }
                )
                current = (
                    date(current.year + 1, 1, 1)
                    if current.month == 12
                    else date(current.year, current.month + 1, 1)
                )
            jalali_from = gregorian_to_jalali(date_from)
            jalali_to = gregorian_to_jalali(date_to)
            for value in (jalali_from, jalali_to):
                year, month, _ = value.split("/")
                target_years.add(year)
                target_months.update({f"{year}-{month}", f"{year}/{month}", f"{year}{month}"})

        def score(reference: SitemapReference) -> tuple[int, str]:
            lowered = reference.url.casefold()
            value = 0
            if any(month in lowered for month in target_months):
                value -= 12
            elif any(year in lowered for year in target_years):
                value -= 8
            explicit_years = set(
                re.findall(r"(?<!\d)(?:1[34]\d{2}|(?:19|20)\d{2})(?!\d)", lowered)
            )
            if target_years and explicit_years and explicit_years.isdisjoint(target_years):
                value += 15
            if any(
                marker in lowered
                for marker in ("news", "article", "post", "story", "fa", "persian")
            ):
                value -= 3
            if any(marker in lowered for marker in ("video", "image", "tag", "author")):
                value += 4
            if reference.last_modified and date_from and date_to:
                modified = _as_utc(reference.last_modified).date()
                if date_from <= modified <= date_to:
                    value -= 5
                elif modified < date_from:
                    value += 2
            return value, reference.url

        return sorted(references, key=score)

    @staticmethod
    def _reference_matches_range(
        reference: SitemapReference,
        *,
        date_from: date | None,
        date_to: date | None,
    ) -> bool:
        """Reject sitemap branches that are provably outside the monitoring range."""
        if not date_from or not date_to:
            return True
        if reference.last_modified:
            modified = _as_utc(reference.last_modified).date()
            if modified < date_from:
                return False

        lowered = reference.url.casefold()
        explicit_years = {
            int(value)
            for value in re.findall(
                r"(?<!\d)(1[34]\d{2}|(?:19|20)\d{2})(?!\d)", lowered
            )
        }
        gregorian_years = set(range(date_from.year, date_to.year + 1))
        jalali_years = {
            int(gregorian_to_jalali(value).split("/", 1)[0])
            for value in (date_from, date_to)
        }
        return not explicit_years or bool(explicit_years & (gregorian_years | jalali_years))

    async def discover(
        self,
        source_url: str,
        topic: str,
        seed_urls: list[str],
        fetch: HttpGet,
        date_from: date | None,
        date_to: date | None,
    ) -> list[DiscoveredLink]:
        seed_references = [
            SitemapReference(canonicalize_url(url)) for url in dict.fromkeys(seed_urls)
        ]
        queue = deque(
            (reference.url, 0)
            for reference in self.prioritize_references(
                seed_references,
                date_from=date_from,
                date_to=date_to,
            )
        )
        visited: set[str] = set()
        discovered: list[DiscoveredLink] = []
        semaphore = asyncio.Semaphore(self._policy.max_concurrency)

        async def read_sitemap(url: str, depth: int):
            try:
                async with semaphore:
                    response = await fetch(url)
                if response.status_code >= 400:
                    return [], []
                text = response_text(response)
                content_type = response.headers.get("content-type", "").lower()
                xml_start = text.lstrip()[:200].lower()
                if not (
                    "xml" in content_type
                    or "gzip" in content_type
                    or xml_start.startswith("<?xml")
                    or xml_start.startswith("<urlset")
                    or xml_start.startswith("<sitemapindex")
                ):
                    return [], []
                references = discover_sitemap_references(
                    text,
                    str(response.url),
                    self._policy.allow_subdomains,
                )
                links = discover_from_xml(
                    text,
                    source_url,
                    topic,
                    "sitemap",
                    self._policy.allow_subdomains,
                )
                links = [
                    link
                    for link in links
                    if link.published_at is None
                    or (
                        (date_from is None or _as_utc(link.published_at).date() >= date_from)
                        and (date_to is None or _as_utc(link.published_at).date() <= date_to)
                    )
                ]
                return references if depth < self._policy.max_sitemap_depth else [], links
            except Exception:
                return [], []

        while queue and len(visited) < self._policy.max_sitemaps:
            batch: list[tuple[str, int]] = []
            while queue and len(batch) < self._policy.max_concurrency:
                if len(visited) >= self._policy.max_sitemaps:
                    break
                url, depth = queue.popleft()
                if url in visited or urlparse(url).scheme not in {"http", "https"}:
                    continue
                visited.add(url)
                batch.append((url, depth))
            if not batch:
                continue
            results = await asyncio.gather(*(read_sitemap(url, depth) for url, depth in batch))
            for (_, depth), (references, links) in zip(batch, results, strict=True):
                discovered.extend(links)
                for reference in reversed(
                    self.prioritize_references(
                        references,
                        date_from=date_from,
                        date_to=date_to,
                    )
                ):
                    if (
                        reference.url not in visited
                        and self._reference_matches_range(
                            reference, date_from=date_from, date_to=date_to
                        )
                    ):
                        queue.appendleft((reference.url, depth + 1))
            if len(discovered) >= self._policy.max_discovered_urls:
                break
        return discovered[: self._policy.max_discovered_urls]
