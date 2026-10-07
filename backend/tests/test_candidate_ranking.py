from datetime import UTC, date, datetime

from app.crawler.schemas import CrawlPolicy, DiscoveredLink
from app.modules.crawling.infrastructure.candidate_ranker import CandidateRanker


def _link(
    name: str,
    provider: str,
    *,
    topic: bool = True,
    published: datetime | None = None,
    methods: list[str] | None = None,
) -> DiscoveredLink:
    return DiscoveredLink(
        url=f"https://example.com/news/{name}",
        title=name,
        discovered_via=provider,
        discovery_methods=methods or [provider],
        topic_matches=["topic"] if topic else [],
        published_at=published,
    )


def test_ranker_preserves_provider_diversity_during_search_flood() -> None:
    links = [_link(f"search-{index}", "search") for index in range(20)]
    links.extend(
        [
            _link("rss", "rss"),
            _link("sitemap", "sitemap"),
            _link("gdelt", "gdelt"),
            _link("common", "common_crawl"),
        ]
    )
    ranker = CandidateRanker(
        CrawlPolicy(max_pages=5, candidate_provider_slots=5)
    )

    selected = ranker.rank(links, date_from=None, date_to=None)

    assert {link.discovered_via for link in selected} == {
        "rss",
        "sitemap",
        "search",
        "gdelt",
        "common_crawl",
    }


def test_ranker_prefers_topic_and_verified_date_over_generic_diversity() -> None:
    target_date = datetime(2026, 3, 10, tzinfo=UTC)
    links = [
        _link("generic-html", "html", topic=False),
        _link("matching-rss", "rss", published=target_date),
        _link("dated-rss", "rss", topic=False, published=target_date),
    ]

    selected = CandidateRanker(CrawlPolicy(max_pages=2)).rank(
        links,
        date_from=date(2026, 3, 1),
        date_to=date(2026, 3, 31),
    )

    assert [str(link.url).rsplit("/", 1)[-1] for link in selected] == [
        "matching-rss",
        "dated-rss",
    ]


def test_ranker_rewards_independent_discovery_provenance() -> None:
    links = [
        _link("single", "search", methods=["search"]),
        _link("confirmed", "rss", methods=["rss", "sitemap", "search"]),
    ]
    policy = CrawlPolicy(
        max_pages=1,
        candidate_diversity_enabled=False,
    )

    selected = CandidateRanker(policy).rank(links, date_from=None, date_to=None)

    assert str(selected[0].url).endswith("/confirmed")


def test_ranker_rejects_known_dates_outside_requested_range() -> None:
    links = [
        _link("old", "rss", published=datetime(2025, 1, 1, tzinfo=UTC)),
        _link("unknown", "common_crawl", published=None),
    ]

    selected = CandidateRanker(CrawlPolicy()).rank(
        links,
        date_from=date(2026, 3, 1),
        date_to=date(2026, 3, 31),
    )

    assert [str(link.url).rsplit("/", 1)[-1] for link in selected] == ["unknown"]
