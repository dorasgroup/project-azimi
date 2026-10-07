from urllib.parse import parse_qs, unquote_plus, urlparse

from app.crawler.schemas import CrawlPolicy
from app.modules.crawling.infrastructure.discovery_seeds import DiscoverySeedBuilder
from app.processing.query_planner import TopicQueryPlanner


TOPIC = "بررسی رویکرد رسانه های داخلی و خارجی به مسئله زن در ایران"


def test_query_plan_preserves_canonical_topic_and_all_core_concepts() -> None:
    plan = TopicQueryPlanner().plan(TOPIC)

    assert plan.canonical_topic == TOPIC
    assert plan.concepts == ("زن", "ایران")
    assert plan.queries[:2] == (TOPIC, "زن ایران")
    assert "women iran" in plan.queries
    assert "iranian women" in plan.queries
    assert all(variant.covered_concepts == ("زن", "ایران") for variant in plan.variants)


def test_query_plan_targets_source_languages_without_changing_the_topic() -> None:
    persian = TopicQueryPlanner().plan(TOPIC, ("fa",))
    english = TopicQueryPlanner().plan(TOPIC, ("en",))
    french_fallback = TopicQueryPlanner().plan(TOPIC, ("fr", "en"))

    assert persian.canonical_topic == english.canonical_topic == TOPIC
    assert all(any("\u0600" <= character <= "\u06ff" for character in query) for query in persian.queries)
    assert all(query.isascii() for query in english.queries)
    assert english.queries[0] == "women iran"
    assert french_fallback.queries == english.queries


def test_query_expansion_is_bounded_and_does_not_invent_nuclear_context() -> None:
    topic = "مذاکرات ایران و آمریکا"
    plan = TopicQueryPlanner().plan(topic, ("en",), max_queries=3)

    assert len(plan.queries) <= 3
    assert all("nuclear" not in query.casefold() for query in plan.queries)
    assert all(
        "iran" in query.casefold() or "tehran" in query.casefold()
        for query in plan.queries
    )


def test_foreign_source_search_seeds_use_latin_retrieval_queries() -> None:
    candidates = DiscoverySeedBuilder(CrawlPolicy(), ("en",)).search_candidates(
        "https://www.theguardian.com",
        TOPIC,
        ("en",),
    )
    decoded_queries = {
        unquote_plus(value)
        for url, _ in candidates
        for value in parse_qs(urlparse(url).query).values()
        for value in value
    }

    assert "women iran" in decoded_queries
    assert TOPIC not in decoded_queries


def test_disabled_search_capability_keeps_homepage_without_search_seeds() -> None:
    seeds = DiscoverySeedBuilder(
        CrawlPolicy(max_archive_seeds=0),
        ("en",),
        search_enabled=False,
        archive_enabled=False,
    ).html_seeds(
        "https://www.theguardian.com",
        TOPIC,
        None,
        None,
    )

    assert [seed.url for seed in seeds] == ["https://www.theguardian.com/"]
