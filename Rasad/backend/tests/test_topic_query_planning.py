from app.crawler.discovery import link_matches_topic
from app.processing.scoring import relevance_score
from app.processing.topic import topic_concepts, topic_search_queries


PERSIAN_TOPIC = "رویکرد رسانه های داخلی و خارجی به مسئله زن در ایران"


def test_query_planner_removes_instructional_noise_and_adds_latin_aliases() -> None:
    concepts = topic_concepts(PERSIAN_TOPIC)
    queries = topic_search_queries(PERSIAN_TOPIC)

    assert [concept.label for concept in concepts] == ["زن", "ایران"]
    assert "women iran" in queries


def test_relevance_requires_both_core_concepts_for_multi_concept_topic() -> None:
    iran_only, _ = relevance_score(
        PERSIAN_TOPIC,
        "تحولات ایران",
        "این گزارش فقط درباره ایران و تهران است.",
        "https://example.com/iran",
    )
    women_and_iran, matched = relevance_score(
        PERSIAN_TOPIC,
        "زنان در ایران",
        "این گزارش درباره وضعیت زنان و حقوق زن در ایران است.",
        "https://example.com/women-in-iran",
    )

    assert iran_only == 0
    assert women_and_iran > 0
    assert {"زن", "ایران"}.issubset(set(matched))


def test_persian_women_aliases_match_news_titles() -> None:
    matches = link_matches_topic(
        "https://example.com/news/1",
        "حضور بانوان و زنان در جامعه ایران",
        PERSIAN_TOPIC,
    )

    assert matches == ["ایران", "زن"]


def test_persian_topic_accepts_english_article_only_when_both_concepts_match() -> None:
    accepted, matched = relevance_score(
        PERSIAN_TOPIC,
        "Iranian women continue their campaign",
        "Women in Iran discuss equal rights and social participation.",
        "https://example.com/world/iranian-women",
    )
    rejected, _ = relevance_score(
        PERSIAN_TOPIC,
        "Iran regional update",
        "The report covers diplomacy and the economy in Tehran.",
        "https://example.com/world/iran-update",
    )

    assert accepted > 0
    assert {"women", "iran"}.issubset(set(matched))
    assert rejected == 0
