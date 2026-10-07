"""Render bounded, source-specific discovery seeds from validated configuration."""

from datetime import date
from urllib.parse import quote_plus, urljoin

from app.crawler.discovery import canonicalize_url
from app.crawler.schemas import SourceDiscoveryProfile
from app.crawler.security import host_is_allowed
from app.processing.dates import gregorian_to_jalali
from app.processing.topic import topic_search_queries


class SourceProfileSeedBuilder:
    """Expand one validated profile into same-host URLs without performing I/O."""

    def __init__(
        self,
        profile: SourceDiscoveryProfile,
        languages: tuple[str, ...] | None = None,
    ) -> None:
        self._profile = profile
        self._languages = languages

    @staticmethod
    def _resolve(source_url: str, candidate: str) -> str | None:
        url = canonicalize_url(urljoin(source_url, candidate))
        return url if host_is_allowed(url, source_url, allow_subdomains=False) else None

    def search_candidates(self, source_url: str, topic: str) -> list[tuple[str, str]]:
        candidates: list[tuple[str, str]] = []
        for query in topic_search_queries(topic, self._languages)[:4]:
            encoded_query = quote_plus(query)
            for template in self._profile.search_templates:
                url = self._resolve(source_url, template.replace("{query}", encoded_query))
                if url:
                    candidates.append((url, "search"))
        return list(dict.fromkeys(candidates))

    def archive_candidates(
        self,
        source_url: str,
        months: list[date],
    ) -> list[tuple[str, str]]:
        candidates: list[tuple[str, str]] = []
        for month in months:
            jalali_year, jalali_month, _ = gregorian_to_jalali(month).split("/")
            replacements = {
                "{year}": str(month.year),
                "{month}": f"{month.month:02d}",
                "{jalali_year}": jalali_year,
                "{jalali_month}": jalali_month,
            }
            for template in self._profile.archive_templates:
                candidate = template
                for placeholder, value in replacements.items():
                    candidate = candidate.replace(placeholder, value)
                url = self._resolve(source_url, candidate)
                if url:
                    candidates.append((url, "archive"))
        return list(dict.fromkeys(candidates))

    def feed_urls(self, source_url: str) -> list[str]:
        return self._resolved_paths(source_url, self._profile.feed_paths)

    def sitemap_urls(self, source_url: str) -> list[str]:
        return self._resolved_paths(source_url, self._profile.sitemap_paths)

    def _resolved_paths(self, source_url: str, paths: list[str]) -> list[str]:
        return list(
            dict.fromkeys(
                url
                for path in paths
                for url in [self._resolve(source_url, path)]
                if url is not None
            )
        )
