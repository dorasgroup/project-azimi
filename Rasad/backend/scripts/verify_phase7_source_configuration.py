"""Deterministic acceptance check for validated source-specific profiles."""

import json
from datetime import date

from pydantic import ValidationError

from app.crawler.schemas import DiscoveredLink
from app.modules.crawling.infrastructure.discovery_seeds import DiscoverySeedBuilder
from app.modules.crawling.infrastructure.url_admission import SourceUrlAdmissionPolicy
from app.schemas.source import SourceCreate


SOURCE = "https://news.example.com"


def verify() -> dict[str, object]:
    configured = SourceCreate.model_validate(
        {
            "title": "Example News",
            "base_url": SOURCE,
            "type": "news_site",
            "crawl_config": {
                "crawl_policy": {
                    "max_archive_seeds": 12,
                    "discovery_profile": {
                        "replace_default_seeds": True,
                        "feed_paths": ["/api/rss"],
                        "sitemap_paths": ["/news/sitemap.xml"],
                        "search_templates": ["/find?term={query}"],
                        "archive_templates": [
                            "/news/{year}/{month}/",
                            "/archive/{jalali_year}/{jalali_month}/",
                        ],
                        "include_url_globs": ["/articles/*"],
                        "exclude_url_globs": ["*/video/*"],
                    },
                },
                "processing_policy": {"min_text_length": 100},
            },
        }
    )
    policy = configured.crawl_config.crawl_policy
    assert policy is not None
    builder = DiscoverySeedBuilder(policy)
    seeds = builder.html_seeds(
        SOURCE,
        "women iran",
        date(2026, 3, 1),
        date(2026, 3, 31),
    )
    admission = SourceUrlAdmissionPolicy(policy.discovery_profile)
    admitted = admission.filter(
        [
            DiscoveredLink(
                url=f"{SOURCE}/articles/women-iran",
                discovered_via="sitemap",
            ),
            DiscoveredLink(
                url=f"{SOURCE}/articles/video/women-iran",
                discovered_via="sitemap",
            ),
            DiscoveredLink(url=f"{SOURCE}/about", discovered_via="html"),
        ]
    )
    invalid_cases = 0
    for crawl_config in (
        {"unknown": {}},
        {"crawl_policy": {"max_pages": 0}},
        {
            "crawl_policy": {
                "discovery_profile": {
                    "feed_paths": ["https://outside.example/rss.xml"]
                }
            }
        },
    ):
        try:
            SourceCreate.model_validate(
                {
                    "title": "Invalid",
                    "base_url": SOURCE,
                    "type": "news_site",
                    "crawl_config": crawl_config,
                }
            )
        except ValidationError:
            invalid_cases += 1

    stored = configured.crawl_config.storage_dict()
    result = {
        "phase": 7,
        "status": "passed",
        "custom_feed": builder.feed_urls(SOURCE),
        "custom_sitemap": builder.default_sitemap_urls(SOURCE),
        "search_and_archive_seed_count": len(seeds),
        "admitted_urls": [str(link.url) for link in admitted],
        "invalid_configurations_rejected": invalid_cases,
        "stored_top_level_keys": sorted(stored),
        "database_writes": 0,
        "network_calls": 0,
        "analysis_provider_calls": 0,
    }
    assert result["custom_feed"] == [f"{SOURCE}/api/rss"]
    assert result["custom_sitemap"] == [f"{SOURCE}/news/sitemap.xml"]
    assert result["admitted_urls"] == [f"{SOURCE}/articles/women-iran"]
    assert invalid_cases == 3
    assert sorted(stored) == ["crawl_policy", "processing_policy"]
    return result


if __name__ == "__main__":
    print(json.dumps(verify(), ensure_ascii=False, indent=2))
