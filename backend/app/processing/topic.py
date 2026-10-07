from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote_plus

from app.processing.text import normalize_text, tokens


@dataclass(frozen=True)
class TopicConcept:
    label: str
    tokens: frozenset[str]
    phrases: frozenset[str]
    preferred_latin: str | None = None


PERSIAN_STOPWORDS = {
    "از",
    "به",
    "در",
    "و",
    "با",
    "برای",
    "بر",
    "تا",
    "یا",
    "را",
    "که",
    "های",
    "رویکرد",
    "بررسی",
    "تحلیل",
    "مسئله",
    "رسانه",
    "رسانه‌ها",
    "داخلی",
    "خارجی",
    "اخبار",
    "خبر",
    "دنیای",
    "گزارش",
    "آخرین",
    "روز",
    "سایت",
    "پایگاه",
}


TOPIC_ALIASES: dict[str, tuple[set[str], set[str], str | None]] = {
    "فوتبال": (
        {"football", "soccer", "دربی", "ورزش", "ورزشی", "لیگ", "تیم", "مسابقه", "مسابقات"},
        {"لیگ برتر", "جام جهانی", "تیم ملی", "تیم فوتبال", "جام ملت‌ها", "باشگاه"},
        "football",
    ),
    "ورزش": (
        {"sport", "sports", "فوتبال", "والیبال", "کشتی", "ورزشی"},
        {"اخبار ورزشی", "ورزش جهان", "ورزش ایران"},
        "sport",
    ),

    "زن": (
        {"زنان", "بانوان", "دختران", "woman", "women", "female", "gender"},
        {"حقوق زن", "حقوق زنان", "women's rights", "women rights"},
        "women",
    ),
    "ایران": ({"iran", "iranian", "tehran"}, {"islamic republic"}, "iran"),
    "آمریکا": (
        {"america", "american", "usa", "us", "washington"},
        {"united states", "united states of america"},
        "us",
    ),
    "امریکا": (
        {"america", "american", "usa", "us", "washington"},
        {"united states", "united states of america"},
        "us",
    ),
    "ایالات": ({"usa", "us", "washington"}, {"united states", "united states of america"}, "us"),
    "متحده": ({"usa", "us", "washington"}, {"united states", "united states of america"}, "us"),
    "مذاکرات": ({"talks", "negotiations", "negotiation", "diplomacy", "dialogue"}, set(), "talks"),
    "مذاکره": ({"talks", "negotiations", "negotiation", "diplomacy", "dialogue"}, set(), "talks"),
    "گفتگو": ({"talks", "negotiations", "dialogue", "diplomacy"}, set(), "talks"),
    "گفت‌وگو": ({"talks", "negotiations", "dialogue", "diplomacy"}, set(), "talks"),
    "هسته": ({"nuclear"}, set(), "nuclear"),
    "هسته‌ای": ({"nuclear"}, set(), "nuclear"),
    "برجام": ({"jcpoa", "nuclear", "deal"}, {"nuclear deal"}, "jcpoa"),
    "تحریم": ({"sanction", "sanctions"}, set(), "sanctions"),
    "تحریم‌ها": ({"sanction", "sanctions"}, set(), "sanctions"),
    "ترامپ": ({"trump"}, set(), "trump"),
    "اسرائیل": ({"israel", "israeli"}, set(), "israel"),
    "اسراییل": ({"israel", "israeli"}, set(), "israel"),
    "حمله": (
        {
            "attack",
            "attacks",
            "strike",
            "strikes",
            "airstrike",
            "airstrikes",
            "missile",
            "missiles",
            "assault",
        },
        {"missile attack", "missile strike", "air strike"},
        "attack",
    ),
    "حملات": (
        {
            "attack",
            "attacks",
            "strike",
            "strikes",
            "airstrike",
            "airstrikes",
            "missile",
            "missiles",
            "assault",
        },
        {"missile attacks", "missile strikes", "air strikes"},
        "attack",
    ),
    "تهاجم": ({"attack", "attacks", "offensive", "assault", "strike", "strikes"}, set(), "attack"),
    "موشک": ({"missile", "missiles", "rocket", "rockets"}, set(), "missile"),
    "موشکی": ({"missile", "missiles", "rocket", "rockets"}, set(), "missile"),
    "پهپاد": ({"drone", "drones", "uav"}, set(), "drone"),
    "پهباد": ({"drone", "drones", "uav"}, set(), "drone"),
    "جنگ": ({"war", "conflict"}, set(), "war"),
    "سیاست": ({"policy", "politics", "political"}, set(), "policy"),
}


def topic_concepts(topic: str) -> list[TopicConcept]:
    concepts: list[TopicConcept] = []
    seen: set[str] = set()
    for term in tokens(topic):
        if term in PERSIAN_STOPWORDS:
            continue
        if term in seen:
            continue
        seen.add(term)
        alias_tokens, alias_phrases, preferred_latin = TOPIC_ALIASES.get(term, (set(), set(), None))
        concepts.append(
            TopicConcept(
                label=term,
                tokens=frozenset({term, *alias_tokens}),
                phrases=frozenset(alias_phrases),
                preferred_latin=preferred_latin,
            )
        )
    return concepts


def topic_search_queries(
    topic: str,
    languages: tuple[str, ...] | None = None,
) -> list[str]:
    from app.processing.query_planner import query_plan

    return list(query_plan(topic, languages).queries)


def encoded_topic_search_queries(topic: str) -> list[str]:
    return [quote_plus(query) for query in topic_search_queries(topic)]


def concept_hits(
    concept: TopicConcept,
    *,
    token_sets: list[set[str]],
    texts: list[str],
) -> set[str]:
    hits: set[str] = set()
    for token_set in token_sets:
        hits.update(concept.tokens & token_set)
    normalized_texts = [normalize_text(text).casefold() for text in texts if text]
    for phrase in concept.phrases:
        phrase_normalized = normalize_text(phrase).casefold()
        if any(phrase_normalized in text for text in normalized_texts):
            hits.add(phrase)
    return hits
