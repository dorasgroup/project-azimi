from datetime import date

import pytest
from pydantic import ValidationError

from app.crawler.schemas import (
    CrawlPolicy,
    DiscoveredLink,
    SourceDiscoveryProfile,
)
from app.modules.crawling.infrastructure.discovery_seeds import DiscoverySeedBuilder
from app.modules.crawling.infrastructure.url_admission import SourceUrlAdmissionPolicy


def _policy() -> CrawlPolicy:
    return CrawlPolicy(
        max_archive_seeds=20,
        discovery_profile=SourceDiscoveryProfile(
            replace_default_seeds=True,
            feed_paths=["/api/rss", "https://outside.example/rss"],
            sitemap_paths=["/news/sitemap.xml"],
            search_templates=["/find?term={query}"],
            archive_templates=[
                "/news/{year}/{month}/",
                "/archive/{jalali_year}/{jalali_month}/",
            ],
            include_url_globs=["https://news.example.com/articles/*"],
            exclude_url_globs=["*/video/*"],
        ),
    )


def test_profile_rejects_unknown_or_missing_template_placeholders() -> None:
    with pytest.raises(ValidationError):
        SourceDiscoveryProfile(search_templates=["/search/static"])
    with pytest.raises(ValidationError):
        SourceDiscoveryProfile(archive_templates=["/archive/{unknown}/"])


def test_profile_replaces_defaults_and_keeps_custom_seeds_on_source_host() -> None:
    builder = DiscoverySeedBuilder(_policy())
    source = "https://news.example.com"

    feeds = builder.feed_urls(source)
    sitemaps = builder.default_sitemap_urls(source)
    html = builder.html_seeds(
        source,
        "Iran talks",
        date(2026, 3, 1),
        date(2026, 3, 31),
    )

    assert feeds == ["https://news.example.com/api/rss"]
    assert sitemaps == ["https://news.example.com/news/sitemap.xml"]
    assert "https://news.example.com/" not in {seed.url for seed in html}
    assert any(seed.url.startswith("https://news.example.com/find?") for seed in html)
    assert any("/news/2026/03/" in seed.url for seed in html)
    assert any("/archive/1404/12/" in seed.url for seed in html)
    assert all("outside.example" not in seed.url for seed in html)


def test_profile_extends_default_seeds_when_replace_is_disabled() -> None:
    policy = CrawlPolicy(
        discovery_profile=SourceDiscoveryProfile(
            feed_paths=["/custom-feed.xml"],
            sitemap_paths=["/custom-map.xml"],
        )
    )
    builder = DiscoverySeedBuilder(policy)

    assert builder.feed_urls("https://example.com")[-1] == (
        "https://example.com/custom-feed.xml"
    )
    assert builder.default_sitemap_urls("https://example.com")[-1] == (
        "https://example.com/custom-map.xml"
    )


def test_url_admission_applies_include_then_exclude_rules() -> None:
    policy = SourceUrlAdmissionPolicy(_policy().discovery_profile)
    links = [
        DiscoveredLink(
            url="https://news.example.com/articles/accepted",
            discovered_via="html",
        ),
        DiscoveredLink(
            url="https://news.example.com/articles/video/rejected",
            discovered_via="html",
        ),
        DiscoveredLink(
            url="https://news.example.com/about",
            discovered_via="html",
        ),
    ]

    assert [str(link.url) for link in policy.filter(links)] == [
        "https://news.example.com/articles/accepted"
    ]
