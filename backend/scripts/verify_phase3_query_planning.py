"""Emit deterministic acceptance evidence for phase-three query planning."""

import json

from app.modules.crawling.infrastructure.source_profile_registry import SOURCE_PROFILES
from app.processing.query_planner import TopicQueryPlanner

BENCHMARK_TOPICS = (
    "مذاکرات ایران و آمریکا",
    "بررسی رویکرد رسانه های داخلی و خارجی به مسئله زن در ایران",
    "حملات ایران و اسرائیل",
)


def verify() -> dict[str, object]:
    planner = TopicQueryPlanner()
    plans = []
    for topic in BENCHMARK_TOPICS:
        for languages in (("fa",), ("en",), ("fa", "en")):
            plan = planner.plan(topic, languages)
            assert plan.canonical_topic == topic
            assert 1 <= len(plan.queries) <= 8
            plans.append(
                {
                    "topic": topic,
                    "languages": list(languages),
                    "concepts": list(plan.concepts),
                    "queries": list(plan.queries),
                }
            )

    profile_checks = []
    for profile in SOURCE_PROFILES:
        plan = planner.plan(BENCHMARK_TOPICS[1], profile.languages)
        assert plan.canonical_topic == BENCHMARK_TOPICS[1]
        assert plan.queries
        profile_checks.append(
            {
                "profile": profile.key,
                "languages": list(profile.languages),
                "query_count": len(plan.queries),
            }
        )
    return {
        "phase": 3,
        "status": "passed",
        "canonical_topic_preserved": True,
        "max_queries": 8,
        "plans": plans,
        "profile_checks": profile_checks,
    }


if __name__ == "__main__":
    print(json.dumps(verify(), ensure_ascii=False, indent=2))
