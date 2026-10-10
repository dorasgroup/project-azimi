"""Deterministic URL deduplication and discovery provenance merging."""

from app.crawler.discovery import canonicalize_url
from app.crawler.schemas import DiscoveredLink


METHOD_PRIORITY = (
    "rss",
    "sitemap",
    "search",
    "archive",
    "html",
    "gdelt",
    "common_crawl",
)


def merge_discovered_links(
    groups: list[list[DiscoveredLink]],
    *,
    limit: int,
) -> list[DiscoveredLink]:
    merged: dict[str, DiscoveredLink] = {}
    for links in groups:
        for candidate in links:
            url = canonicalize_url(str(candidate.url))
            existing = merged.get(url)
            methods = set(candidate.discovery_methods or [candidate.discovered_via])
            if existing is not None:
                methods.update(existing.discovery_methods or [existing.discovered_via])
            ordered_methods = [method for method in METHOD_PRIORITY if method in methods]
            ordered_methods.extend(sorted(methods.difference(ordered_methods)))
            merged[url] = DiscoveredLink(
                url=url,
                title=(
                    candidate.title
                    if candidate.title and (not existing or not existing.title)
                    else existing.title
                    if existing
                    else None
                ),
                discovered_via=ordered_methods[0],
                published_at=(
                    existing.published_at
                    if existing and existing.published_at is not None
                    else candidate.published_at
                ),
                topic_matches=sorted(
                    set(candidate.topic_matches)
                    | (set(existing.topic_matches) if existing else set())
                ),
                discovery_methods=ordered_methods,
            )
            if len(merged) >= limit:
                return list(merged.values())
    return list(merged.values())
