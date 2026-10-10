"""Build bounded discovery seeds for feeds, search, archive and sitemaps."""

from dataclasses import dataclass
from datetime import date
from urllib.parse import quote_plus, urljoin, urlparse

from app.crawler.discovery import canonicalize_url
from app.crawler.schemas import CrawlPolicy
from app.processing.dates import gregorian_to_jalali
from app.processing.topic import topic_search_queries
from app.modules.crawling.infrastructure.source_profile import SourceProfileSeedBuilder


@dataclass(frozen=True)
class DiscoverySeed:
    url: str
    via: str


def _month_starts(date_from: date, date_to: date) -> list[date]:
    current = date(date_from.year, date_from.month, 1)
    final = date(date_to.year, date_to.month, 1)
    months: list[date] = []
    while current <= final:
        months.append(current)
        current = (
            date(current.year + 1, 1, 1)
            if current.month == 12
            else date(current.year, current.month + 1, 1)
        )
    return months


class DiscoverySeedBuilder:
    """Generate deterministic and bounded endpoints for one source."""

    def __init__(
        self,
        policy: CrawlPolicy,
        languages: tuple[str, ...] | None = None,
        *,
        search_enabled: bool = True,
        archive_enabled: bool = True,
    ) -> None:
        self._policy = policy
        self._languages = languages
        self._search_enabled = search_enabled
        self._archive_enabled = archive_enabled
        self._profile = SourceProfileSeedBuilder(policy.discovery_profile, languages)

    @staticmethod
    def search_candidates(
        source_url: str,
        topic: str,
        languages: tuple[str, ...] | None = None,
    ) -> list[tuple[str, str]]:
        candidates: list[tuple[str, str]] = []
        parsed = urlparse(source_url)
        host = parsed.netloc.casefold()
        path = parsed.path.casefold()
        raw_queries = topic_search_queries(topic, languages)
        is_persian_source = (
            host.endswith(".ir")
            or "persian" in path
            or "/fa" in path
            or "parsi" in path
            or "farda" in host
            or "intl" in host
        )
        queries_to_use = list(raw_queries)
        if languages is None and not is_persian_source:
            latin_queries = [query for query in queries_to_use if query.isascii()]
            if latin_queries:
                queries_to_use = latin_queries
        for raw_query in queries_to_use[:4]:
            query = quote_plus(raw_query)
            candidates.extend(
                [
                    (urljoin(source_url, f"/search?q={query}"), "search"),
                    (urljoin(source_url, f"/search?query={query}"), "search"),
                    (urljoin(source_url, f"/?s={query}"), "search"),
                ]
            )
            if is_persian_source:
                candidates.append((urljoin(source_url, f"/fa/search?q={query}"), "search"))
        return list(dict.fromkeys(candidates))

    def archive_candidates(
        self,
        source_url: str,
        date_from: date | None,
        date_to: date | None,
    ) -> list[tuple[str, str]]:
        if not date_from or not date_to or self._policy.max_archive_seeds == 0:
            return []
        host = urlparse(source_url).netloc.casefold()
        is_persian_source = host.endswith(".ir") or any(
            marker in host for marker in ("farsi", "persian", "farda")
        )
        candidates: list[tuple[str, str]] = []
        for month in _month_starts(date_from, date_to):
            year = month.year
            month_number = month.month
            candidates.extend(
                [
                    (urljoin(source_url, f"/{year}/{month_number:02d}/"), "archive"),
                    (urljoin(source_url, f"/archive/{year}/{month_number:02d}/"), "archive"),
                    (
                        urljoin(
                            source_url,
                            f"/archive?year={year}&month={month_number:02d}",
                        ),
                        "archive",
                    ),
                ]
            )
            if is_persian_source:
                jalali_year, jalali_month, _ = gregorian_to_jalali(month).split("/")
                candidates.extend(
                    [
                        (urljoin(source_url, f"/{jalali_year}/{jalali_month}/"), "archive"),
                        (
                            urljoin(source_url, f"/archive/{jalali_year}/{jalali_month}/"),
                            "archive",
                        ),
                    ]
                )
        unique = list(dict.fromkeys(candidates))
        profile_candidates = self._profile.archive_candidates(
            source_url,
            _month_starts(date_from, date_to),
        )
        combined = (
            profile_candidates
            if self._policy.discovery_profile.replace_default_seeds
            else [*unique, *profile_candidates]
        )
        return list(dict.fromkeys(combined))[: self._policy.max_archive_seeds]

    def html_seeds(
        self,
        source_url: str,
        topic: str,
        date_from: date | None,
        date_to: date | None,
    ) -> list[DiscoverySeed]:
        candidates = [] if self._policy.discovery_profile.replace_default_seeds else [(source_url, "html")]
        if self._search_enabled and not self._policy.discovery_profile.replace_default_seeds:
            candidates.extend(self.search_candidates(source_url, topic, self._languages))
        if self._search_enabled:
            candidates.extend(self._profile.search_candidates(source_url, topic))
        if self._archive_enabled:
            candidates.extend(self.archive_candidates(source_url, date_from, date_to))
        return [
            DiscoverySeed(canonicalize_url(url), via)
            for url, via in dict.fromkeys(candidates)
        ]

    def feed_urls(self, source_url: str) -> list[str]:
        defaults = list(
            dict.fromkeys(
                canonicalize_url(urljoin(source_url, path))
                for path in ("/feed", "/rss", "/rss.xml", "/feed.xml")
            )
        )
        profile_urls = self._profile.feed_urls(source_url)
        return list(dict.fromkeys(
            profile_urls
            if self._policy.discovery_profile.replace_default_seeds
            else [*defaults, *profile_urls]
        ))

    def default_sitemap_urls(self, source_url: str) -> list[str]:
        defaults = [
            canonicalize_url(urljoin(source_url, path))
            for path in ("/sitemap_index.xml", "/sitemap.xml")
        ]
        profile_urls = self._profile.sitemap_urls(source_url)
        return list(dict.fromkeys(
            profile_urls
            if self._policy.discovery_profile.replace_default_seeds
            else [*defaults, *profile_urls]
        ))
