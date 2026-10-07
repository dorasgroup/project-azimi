"""Pure normalization rules for source-owned discovery profiles."""

from urllib.parse import urljoin, urlparse

from app.modules.crawling.domain.crawl_policy import SourceDiscoveryProfile


def _same_host(source_url: str, candidate: str) -> bool:
    source_host = (urlparse(source_url).hostname or "").casefold().rstrip(".")
    candidate_host = (urlparse(urljoin(source_url, candidate)).hostname or "").casefold().rstrip(
        "."
    )
    return bool(source_host and candidate_host == source_host)


def normalize_source_discovery_profile(
    source_url: str,
    profile: SourceDiscoveryProfile,
) -> SourceDiscoveryProfile:
    """Reject cross-host seeds and make relative admission globs explicit."""
    endpoint_groups = (
        profile.feed_paths,
        profile.sitemap_paths,
        profile.search_templates,
        profile.archive_templates,
    )
    for candidate in (value for group in endpoint_groups for value in group):
        parsed = urlparse(candidate)
        if parsed.scheme and parsed.scheme not in {"http", "https"}:
            raise ValueError("Source profile endpoints must use HTTP or HTTPS")
        if not _same_host(source_url, candidate):
            raise ValueError("Source profile endpoints must remain on the source host")

    normalized_globs: dict[str, list[str]] = {}
    for field_name, patterns in (
        ("include_url_globs", profile.include_url_globs),
        ("exclude_url_globs", profile.exclude_url_globs),
    ):
        normalized: list[str] = []
        for pattern in patterns:
            parsed = urlparse(pattern)
            if parsed.scheme and parsed.scheme not in {"http", "https"}:
                raise ValueError("Source profile URL globs must use HTTP or HTTPS")
            if not _same_host(source_url, pattern):
                raise ValueError("Source profile URL globs must remain on the source host")
            resolved = pattern if parsed.scheme else urljoin(source_url, pattern)
            if resolved not in normalized:
                normalized.append(resolved)
        normalized_globs[field_name] = normalized

    return profile.model_copy(update=normalized_globs)
