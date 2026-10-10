from datetime import UTC, date, datetime

from app.crawler.schemas import CrawlPolicy, DiscoveredLink
from app.modules.crawling.infrastructure.bm25_lexical_scorer import Bm25LexicalScorer
from app.modules.crawling.infrastructure.candidate_ranker import CandidateRanker, ranking_trace

TOPIC = "بررسی رویکرد رسانه ها به مسئله زن در ایران"


def _candidate(
    slug: str,
    title: str,
    provider: str,
    matches: list[str],
    *,
    published_at: datetime | None = None,
    methods: list[str] | None = None,
) -> DiscoveredLink:
    return DiscoveredLink(
        url=f"https://example.com/news/{slug}",
        title=title,
        discovered_via=provider,
        discovery_methods=methods or [provider],
        topic_matches=matches,
        published_at=published_at,
    )


def test_bm25_matches_english_title_for_persian_topic() -> None:
    relevant = _candidate(
        "iran-womens-rights",
        "Women's rights in Iran and the campaign for equality",
        "html",
        ["زن", "ایران"],
    )
    generic = _candidate(
        "regional-index",
        "Regional news index",
        "rss",
        [],
    )

    scores = Bm25LexicalScorer().score([generic, relevant], TOPIC)

    assert scores[str(relevant.url)] == 1.0
    assert scores[str(generic.url)] == 0.0


def test_hybrid_ranker_can_prefer_exact_relevance_over_provider_weight() -> None:
    relevant = _candidate(
        "iran-womens-rights",
        "Women's rights in Iran and the campaign for equality",
        "html",
        ["زن", "ایران"],
    )
    generic = _candidate(
        "iran-women-index",
        "Iran and women daily news index",
        "rss",
        ["زن", "ایران"],
    )
    partial = _candidate(
        "iran-economy",
        "Iran economy and diplomacy",
        "rss",
        ["ایران"],
    )
    policy = CrawlPolicy(max_pages=2, candidate_diversity_enabled=False)

    selected = CandidateRanker(policy, hybrid_enabled=True).rank(
        [generic, partial, relevant],
        date_from=None,
        date_to=None,
        topic=TOPIC,
    )

    assert selected[0] == relevant
    assert partial not in selected


def test_hybrid_ranking_trace_explains_components_and_selection() -> None:
    ranker = CandidateRanker(
        CrawlPolicy(max_pages=1, candidate_diversity_enabled=False),
        hybrid_enabled=True,
    )
    candidate = _candidate(
        "iran-womens-rights",
        "Women's rights in Iran",
        "rss",
        ["زن", "ایران"],
        published_at=datetime(2026, 3, 10, tzinfo=UTC),
        methods=["rss", "sitemap"],
    )

    ranker.rank(
        [candidate],
        date_from=date(2026, 3, 1),
        date_to=date(2026, 3, 31),
        topic=TOPIC,
    )
    trace = ranking_trace(ranker)

    assert trace["enabled"] is True
    assert trace["topic_preserved"] is True
    assert trace["selected_count"] == 1
    assert set(trace["top_candidates"][0]["score"]) == {
        "lexical",
        "semantic",
        "evidence",
        "temporal",
        "structure",
        "total",
    }


def test_hybrid_ranker_has_immediate_legacy_rollback() -> None:
    html_exact = _candidate(
        "iran-womens-rights",
        "Women's rights in Iran and equality",
        "html",
        ["زن", "ایران"],
    )
    rss_generic = _candidate(
        "iran-women-index",
        "Iran and women daily news index",
        "rss",
        ["زن", "ایران"],
    )
    policy = CrawlPolicy(max_pages=1, candidate_diversity_enabled=False)

    selected = CandidateRanker(policy, hybrid_enabled=False).rank(
        [html_exact, rss_generic],
        date_from=None,
        date_to=None,
        topic=TOPIC,
    )

    assert selected == [rss_generic]
    assert ranking_trace(CandidateRanker(policy, hybrid_enabled=False))["enabled"] is False


def test_hybrid_ranking_improves_precision_at_three_on_labeled_fixture() -> None:
    links = [
        _candidate("rights", "Women's rights in Iran and equality", "html", ["زن", "ایران"]),
        _candidate("protests", "Iranian women protest mandatory dress rules", "common_crawl", ["زن", "ایران"]),
        _candidate("policy", "Women's rights advocates in Iran seek legal reform", "sitemap", ["زن", "ایران"]),
        _candidate("index", "Iran women daily news index", "rss", ["زن", "ایران"]),
        _candidate("economy", "Iran economy and diplomacy", "rss", ["ایران"]),
        _candidate("health", "Women's global health briefing", "sitemap", ["زن"]),
        _candidate("football", "Iran women football results", "search", ["زن", "ایران"]),
        _candidate("archive", "Archive page", "archive", []),
    ]
    relevant = {"rights", "protests", "policy"}
    policy = CrawlPolicy(max_pages=3, candidate_diversity_enabled=False)

    legacy = CandidateRanker(policy, hybrid_enabled=False).rank(
        links, date_from=None, date_to=None, topic=TOPIC
    )
    hybrid = CandidateRanker(policy, hybrid_enabled=True).rank(
        links, date_from=None, date_to=None, topic=TOPIC
    )
    def precision(ranked: list[DiscoveredLink]) -> float:
        return sum(
            str(link.url).rsplit("/", 1)[-1] in relevant for link in ranked
        ) / len(ranked)

    assert precision(hybrid) > precision(legacy)
    assert precision(hybrid) == 1.0


def test_hybrid_provider_slots_follow_quality_while_preserving_diversity() -> None:
    links = [
        _candidate("rights", "Women's rights in Iran and equality", "html", ["زن", "ایران"]),
        _candidate("policy", "Women's rights advocates in Iran seek reform", "sitemap", ["زن", "ایران"]),
        _candidate("protests", "Iranian women protest mandatory dress rules", "common_crawl", ["زن", "ایران"]),
        _candidate("index", "Iran women daily news index", "rss", ["زن", "ایران"]),
    ]
    selected = CandidateRanker(
        CrawlPolicy(max_pages=3, candidate_provider_slots=3),
        hybrid_enabled=True,
    ).rank(links, date_from=None, date_to=None, topic=TOPIC)

    assert selected[0].discovered_via == "html"
    assert {link.discovered_via for link in selected} == {
        "html",
        "sitemap",
        "common_crawl",
    }
