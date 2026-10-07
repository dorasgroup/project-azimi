"""Deterministic, offline acceptance checks for crawl-refactoring phase 8."""

import json
from datetime import UTC, date, datetime

from app.crawler.schemas import CrawlPolicy, DiscoveredLink
from app.modules.crawling.infrastructure.candidate_ranker import (
    CandidateRanker,
    ranking_trace,
)


TOPIC = "بررسی رویکرد رسانه های داخلی و خارجی به مسئله زن در ایران"


def _candidate(
    slug: str,
    provider: str,
    *,
    matches: list[str] | None = None,
    published_at: datetime | None = None,
    methods: list[str] | None = None,
) -> DiscoveredLink:
    return DiscoveredLink(
        url=f"https://news.example.com/articles/{slug}",
        title=slug.replace("-", " "),
        discovered_via=provider,
        discovery_methods=methods or [provider],
        topic_matches=matches if matches is not None else ["زن", "ایران"],
        published_at=published_at,
    )


def verify() -> dict[str, object]:
    """Verify final selection guarantees without network, database, or model I/O."""
    in_range = datetime(2026, 3, 10, tzinfo=UTC)
    out_of_range = datetime(2025, 3, 10, tzinfo=UTC)
    links = [_candidate(f"search-{index:02d}", "search") for index in range(30)]
    links.extend(
        [
            _candidate("rss-confirmed", "rss", published_at=in_range),
            _candidate("sitemap-confirmed", "sitemap", published_at=in_range),
            _candidate("archive-confirmed", "archive", published_at=in_range),
            _candidate("common-crawl-unknown-date", "common_crawl"),
            _candidate("old-rss", "rss", published_at=out_of_range),
        ]
    )
    policy = CrawlPolicy(max_pages=5, candidate_provider_slots=5)

    def select() -> tuple[list[DiscoveredLink], dict[str, object]]:
        ranker = CandidateRanker(policy, hybrid_enabled=True)
        selected = ranker.rank(
            links,
            date_from=date(2026, 3, 1),
            date_to=date(2026, 3, 31),
            topic=TOPIC,
        )
        return selected, ranking_trace(ranker)

    first, trace = select()
    second, _ = select()
    first_urls = [str(link.url) for link in first]
    second_urls = [str(link.url) for link in second]
    selected_providers = {link.discovered_via for link in first}

    assert first_urls == second_urls, "Candidate ordering must be deterministic"
    assert len(first) == policy.max_pages
    assert {"rss", "sitemap", "search", "archive", "common_crawl"} <= selected_providers
    assert not any(url.endswith("/old-rss") for url in first_urls)
    assert any(url.endswith("/common-crawl-unknown-date") for url in first_urls)
    assert trace["enabled"] is True
    assert trace["topic_preserved"] is True
    assert trace["candidate_count"] == len(links)
    assert trace["eligible_count"] == len(links) - 1
    assert trace["selected_count"] == policy.max_pages

    return {
        "phase": 8,
        "status": "passed",
        "mode": "deterministic_offline",
        "candidate_count": len(links),
        "eligible_count": trace["eligible_count"],
        "selected_count": len(first),
        "selected_providers": sorted(selected_providers),
        "provider_flood_count": 30,
        "provider_diversity_preserved": True,
        "known_out_of_range_rejected": True,
        "unknown_date_preserved_for_fetch": True,
        "deterministic_order": True,
        "canonical_topic_preserved": trace["topic_preserved"],
        "network_calls": 0,
        "database_writes": 0,
        "analysis_provider_calls": 0,
        "live_egress_acceptance": "deferred_until_server_deployment",
    }


if __name__ == "__main__":
    print(json.dumps(verify(), ensure_ascii=False, indent=2, sort_keys=True))
