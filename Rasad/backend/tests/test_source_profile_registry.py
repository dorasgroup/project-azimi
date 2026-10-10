from app.crawler.schemas import CrawlPolicy
from app.modules.crawling.domain.source_profiles import DiscoveryMethod
from app.modules.crawling.infrastructure.source_profile_registry import (
    SOURCE_PROFILES,
    SourceProfileRegistry,
    SourceStrategyRouter,
)


CONFIGURED_SOURCE_URLS = (
    "https://www.bbc.com/persian",
    "https://snn.ir",
    "https://www.theatlantic.com",
    "https://www.aljazeera.net",
    "https://www.economist.com",
    "https://www.iranintl.com",
    "https://www.irna.ir",
    "https://www.yjc.ir",
    "https://www.bbc.com",
    "https://time.com",
    "https://tasnimnews.ir",
    "https://www.isna.ir",
    "https://www.khabaronline.ir",
    "https://www.dw.com",
    "https://www.dw.com/fa-ir",
    "https://www.radiofarda.com",
    "https://ir.voanews.com",
    "https://www.farsnews.ir",
    "https://www.foreignaffairs.com",
    "https://www.ft.com",
    "https://www.lemonde.fr/en",
    "https://www.nytimes.com",
    "https://www.haaretz.com",
    "https://www.washingtonpost.com",
    "https://www.wsj.com",
    "https://www.alef.ir",
    "https://www.politico.eu",
    "https://www.theguardian.com",
    "https://parsi.euronews.com/",
    "https://www.sharghdaily.com",
)


def test_registry_covers_every_news_source_in_the_catalog() -> None:
    registry = SourceProfileRegistry()

    resolved = [registry.resolve(url) for url in CONFIGURED_SOURCE_URLS]

    assert all(profile.key != "generic" for profile in resolved)
    assert len(SOURCE_PROFILES) == 28


def test_registry_resolves_aliases_and_has_a_generic_fallback() -> None:
    registry = SourceProfileRegistry()

    assert registry.resolve("https://www.sharghdaily.ir/news/1").key == "shargh"
    assert registry.resolve("https://unknown-news.example/path").key == "generic"


def test_router_enables_only_declared_capabilities_and_merges_seeds() -> None:
    plan = SourceStrategyRouter().plan("https://www.economist.com", CrawlPolicy())

    assert plan.profile.content_restricted is True
    assert plan.profile.remote_egress_required is True
    assert DiscoveryMethod.FEED in plan.enabled
    assert DiscoveryMethod.ARCHIVE not in plan.enabled
    assert "archive" in plan.skipped
    assert DiscoveryMethod.SEARCH not in plan.enabled
    assert "search" in plan.skipped
    assert "/the-world-this-week/rss.xml" in plan.policy.discovery_profile.feed_paths
    assert plan.policy.historical_discovery_enabled is True
