"""Bounded multilingual retrieval queries that never replace the user's topic."""

import re
from dataclasses import dataclass

from app.processing.text import normalize_text

_PERSIAN_PATTERN = re.compile(r"[\u0600-\u06ff]")
_RETRIEVAL_NOISE = {"ها"}


@dataclass(frozen=True)
class QueryVariant:
    text: str
    language: str
    kind: str
    covered_concepts: tuple[str, ...]


@dataclass(frozen=True)
class TopicQueryPlan:
    canonical_topic: str
    target_languages: tuple[str, ...]
    concepts: tuple[str, ...]
    variants: tuple[QueryVariant, ...]

    @property
    def queries(self) -> tuple[str, ...]:
        return tuple(variant.text for variant in self.variants)


class TopicQueryPlanner:
    """Build high-recall queries while preserving all canonical core concepts."""

    def plan(
        self,
        topic: str,
        languages: tuple[str, ...] | None = None,
        *,
        max_queries: int = 8,
    ) -> TopicQueryPlan:
        from app.processing.topic import topic_concepts

        canonical = " ".join(topic.split()).strip()
        concepts = [
            concept
            for concept in topic_concepts(canonical)
            if concept.label not in _RETRIEVAL_NOISE
        ]
        labels = tuple(concept.label for concept in concepts)
        requested = tuple(dict.fromkeys(language.casefold() for language in (languages or ("fa", "en"))))
        variants: list[QueryVariant] = []

        def add(text: str, language: str, kind: str) -> None:
            cleaned = " ".join(text.split()).strip()
            key = normalize_text(cleaned).casefold()
            if cleaned and key not in {normalize_text(item.text).casefold() for item in variants}:
                variants.append(QueryVariant(cleaned, language, kind, labels))

        has_persian = bool(_PERSIAN_PATTERN.search(canonical))
        if "fa" in requested and has_persian:
            add(canonical, "fa", "canonical")
            compact = " ".join(labels)
            if compact != canonical:
                add(compact, "fa", "compact")
        if not has_persian and ("en" in requested or not requested):
            add(canonical, "en", "canonical")

        latin = [concept.preferred_latin for concept in concepts]
        if concepts and all(latin) and ("en" in requested or any(lang != "fa" for lang in requested)):
            add(" ".join(term for term in latin if term), "en", "concept_translation")
            self._add_precise_english_variants(add, frozenset(term for term in latin if term))

        if not variants:
            add(canonical, "und", "canonical_fallback")
        return TopicQueryPlan(canonical, requested, labels, tuple(variants[:max_queries]))

    @staticmethod
    def _add_precise_english_variants(add, terms: frozenset[str]) -> None:
        if terms == {"women", "iran"}:
            add("iranian women", "en", "natural_order")
            add("women's rights iran", "en", "phrase_alias")
        if {"iran", "us", "talks"}.issubset(terms):
            add("Iran United States negotiations", "en", "phrase_alias")
            add("Tehran Washington talks", "en", "entity_alias")
        if {"iran", "israel", "attack"}.issubset(terms):
            add("Iran Israel attacks", "en", "inflection")
            add("Iran strikes Israel", "en", "phrase_alias")


def query_plan(topic: str, languages: tuple[str, ...] | None = None) -> TopicQueryPlan:
    return TopicQueryPlanner().plan(topic, languages)
