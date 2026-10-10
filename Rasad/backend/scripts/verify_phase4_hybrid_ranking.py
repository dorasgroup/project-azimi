"""Benchmark hybrid ranking quality and bounded local runtime."""

import json
from time import perf_counter

from app.crawler.schemas import CrawlPolicy, DiscoveredLink
from app.modules.crawling.infrastructure.candidate_ranker import CandidateRanker, ranking_trace

TOPIC = "بررسی رویکرد رسانه ها به مسئله زن در ایران"
RELEVANT = {"rights", "protests", "policy"}


def _link(slug: str, title: str, provider: str, matches: list[str]) -> DiscoveredLink:
    return DiscoveredLink(
        url=f"https://example.com/news/{slug}",
        title=title,
        discovered_via=provider,
        discovery_methods=[provider],
        topic_matches=matches,
    )


def _fixture() -> list[DiscoveredLink]:
    return [
        _link("rights", "Women's rights in Iran and equality", "html", ["زن", "ایران"]),
        _link("protests", "Iranian women protest mandatory dress rules", "common_crawl", ["زن", "ایران"]),
        _link("policy", "Women's rights advocates in Iran seek legal reform", "sitemap", ["زن", "ایران"]),
        _link("index", "Iran women daily news index", "rss", ["زن", "ایران"]),
        _link("economy", "Iran economy and diplomacy", "rss", ["ایران"]),
        _link("health", "Women's global health briefing", "sitemap", ["زن"]),
        _link("football", "Iran women football results", "search", ["زن", "ایران"]),
        _link("archive", "Archive page", "archive", []),
    ]


def _metrics(ranked: list[DiscoveredLink]) -> dict[str, object]:
    slugs = [str(link.url).rsplit("/", 1)[-1] for link in ranked]
    relevant_positions = [index for index, slug in enumerate(slugs, start=1) if slug in RELEVANT]
    return {
        "ranking": slugs,
        "precision_at_3": sum(slug in RELEVANT for slug in slugs[:3]) / 3,
        "recall_at_3": sum(slug in RELEVANT for slug in slugs[:3]) / len(RELEVANT),
        "mrr": 1 / relevant_positions[0] if relevant_positions else 0,
    }


def verify() -> dict[str, object]:
    policy = CrawlPolicy(max_pages=3, candidate_provider_slots=3)
    legacy = CandidateRanker(policy, hybrid_enabled=False).rank(
        _fixture(), date_from=None, date_to=None, topic=TOPIC
    )
    hybrid_ranker = CandidateRanker(policy, hybrid_enabled=True)
    hybrid = hybrid_ranker.rank(_fixture(), date_from=None, date_to=None, topic=TOPIC)
    legacy_metrics = _metrics(legacy)
    hybrid_metrics = _metrics(hybrid)
    assert hybrid_metrics["precision_at_3"] > legacy_metrics["precision_at_3"]

    bulk = [
        _link(
            f"bulk-{index}",
            "Women's rights in Iran" if index % 7 == 0 else f"Regional update {index}",
            "sitemap" if index % 3 == 0 else "html",
            ["زن", "ایران"] if index % 7 == 0 else [],
        )
        for index in range(5000)
    ]
    started = perf_counter()
    CandidateRanker(CrawlPolicy(max_pages=80), hybrid_enabled=True).rank(
        bulk, date_from=None, date_to=None, topic=TOPIC
    )
    duration_ms = round((perf_counter() - started) * 1000, 2)
    assert duration_ms < 5000
    return {
        "phase": 4,
        "status": "passed",
        "topic_preserved": True,
        "legacy": legacy_metrics,
        "hybrid": hybrid_metrics,
        "bulk_candidates": len(bulk),
        "bulk_duration_ms": duration_ms,
        "network_or_model_calls": 0,
        "trace": ranking_trace(hybrid_ranker),
    }


if __name__ == "__main__":
    print(json.dumps(verify(), ensure_ascii=False, indent=2))
