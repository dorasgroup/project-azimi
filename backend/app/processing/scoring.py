from urllib.parse import urlparse

from app.processing.text import normalize_text
from app.processing.text import tokens
from app.processing.topic import concept_hits, topic_concepts


def relevance_score(topic: str, title: str, content: str, url: str) -> tuple[float, list[str]]:
    concepts = topic_concepts(topic)
    if not concepts:
        return 0.0, []
    title_tokens = set(tokens(title))
    content_tokens = tokens(content)
    content_set = set(content_tokens)
    url_tokens = set(tokens(urlparse(url).path.replace("-", " ")))
    title_text = normalize_text(title).casefold()
    content_text = normalize_text(content).casefold()
    url_text = normalize_text(urlparse(url).path.replace("-", " ")).casefold()
    title_hits: set[str] = set()
    content_hits: set[str] = set()
    url_hits: set[str] = set()
    matched_concepts: set[str] = set()
    matched_terms: set[str] = set()
    for concept in concepts:
        title_matches = concept_hits(concept, token_sets=[title_tokens], texts=[title_text])
        content_matches = concept_hits(concept, token_sets=[content_set], texts=[content_text])
        url_matches = concept_hits(concept, token_sets=[url_tokens], texts=[url_text])
        if title_matches:
            title_hits.add(concept.label)
        if content_matches:
            content_hits.add(concept.label)
        if url_matches:
            url_hits.add(concept.label)
        matches = title_matches | content_matches | url_matches
        if matches:
            matched_concepts.add(concept.label)
            matched_terms.update(matches)
    if not matched_concepts:
        return 0.0, []
    required_concepts = 2 if len(concepts) >= 2 else 1
    if len(matched_concepts) < required_concepts:
        return 0.0, sorted(matched_terms)

    title_coverage = len(title_hits) / len(concepts)
    content_coverage = len(content_hits) / len(concepts)
    url_coverage = len(url_hits) / len(concepts)
    searchable_tokens = {token for concept in concepts for token in concept.tokens}
    frequency = min(
        sum(content_tokens.count(term) for term in searchable_tokens)
        / max(len(content_tokens), 1)
        * 20,
        1,
    )
    score = 0.5 * title_coverage + 0.3 * content_coverage + 0.1 * url_coverage + 0.1 * frequency
    return round(min(score, 1), 4), sorted(matched_terms)
