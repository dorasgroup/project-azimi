from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.enums import SourceCrawlStatus, SourceStatus, SourceType
from app.modules.crawling.infrastructure.source_operations import source_to_read
from app.schemas.source import SourceCreate


def source_payload(crawl_config: dict[str, object]) -> dict[str, object]:
    return {
        "title": "Example News",
        "base_url": "https://news.example.com",
        "type": "news_site",
        "crawl_config": crawl_config,
    }


def test_partial_configuration_stores_only_explicit_values() -> None:
    source = SourceCreate.model_validate(
        source_payload(
            {
                "crawl_policy": {"max_pages": 80, "max_concurrency": 3},
                "processing_policy": {"min_text_length": 100},
            }
        )
    )

    assert source.crawl_config.storage_dict() == {
        "crawl_policy": {"max_pages": 80, "max_concurrency": 3},
        "processing_policy": {"min_text_length": 100},
    }


@pytest.mark.parametrize(
    "crawl_config",
    [
        {"unknown_policy": {}},
        {"crawl_policy": {"max_pages": 0}},
        {"processing_policy": {"min_matched_score": 2}},
        {
            "crawl_policy": {
                "discovery_profile": {"search_templates": ["/search/static"]}
            }
        },
    ],
)
def test_invalid_configuration_is_rejected_before_persistence(
    crawl_config: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        SourceCreate.model_validate(source_payload(crawl_config))


def test_profile_rejects_cross_host_endpoints() -> None:
    with pytest.raises(ValidationError, match="source host"):
        SourceCreate.model_validate(
            source_payload(
                {
                    "crawl_policy": {
                        "discovery_profile": {
                            "feed_paths": ["https://outside.example/rss.xml"]
                        }
                    }
                }
            )
        )


def test_relative_admission_globs_are_normalized_to_source_host() -> None:
    source = SourceCreate.model_validate(
        source_payload(
            {
                "crawl_policy": {
                    "discovery_profile": {
                        "include_url_globs": ["/articles/*"],
                        "exclude_url_globs": ["*/video/*"],
                    }
                }
            }
        )
    )
    profile = source.crawl_config.crawl_policy.discovery_profile  # type: ignore[union-attr]

    assert profile.include_url_globs == ["https://news.example.com/articles/*"]
    assert profile.exclude_url_globs == ["https://news.example.com/*/video/*"]


def test_source_read_exposes_persisted_configuration() -> None:
    stored = {
        "crawl_policy": {
            "discovery_profile": {"feed_paths": ["/custom-rss.xml"]}
        }
    }
    source = SimpleNamespace(
        id=uuid4(),
        title="Example News",
        base_url="https://news.example.com",
        type=SourceType.NEWS_SITE,
        is_active=True,
        crawl_config=stored,
        last_status=SourceStatus.UNKNOWN,
        last_status_message=None,
        last_checked_at=None,
        last_crawl_status=SourceCrawlStatus.UNKNOWN,
        last_crawl_message=None,
        last_crawled_at=None,
    )

    assert source_to_read(source).crawl_config == stored

