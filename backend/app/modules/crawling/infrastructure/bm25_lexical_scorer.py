"""Small dependency-free BM25 scorer for candidate titles and URL paths."""

import math
from collections import Counter
from urllib.parse import unquote, urlparse

from app.crawler.schemas import DiscoveredLink
from app.processing.query_planner import query_plan
from app.processing.text import tokens


class Bm25LexicalScorer:
    """Return normalized lexical relevance for a bounded candidate collection."""

    def score(
        self,
        links: list[DiscoveredLink],
        topic: str,
    ) -> dict[str, float]:
        from app.processing.topic import PERSIAN_STOPWORDS
        documents = [self._document_tokens(link) for link in links]
        raw_queries = [tokens(query) for query in query_plan(topic).queries]
        filtered_queries = [
            [token for token in q if token not in PERSIAN_STOPWORDS]
            for q in raw_queries
        ]
        queries = [q for q in filtered_queries if q] or raw_queries
        if not documents or not any(queries):
            return {str(link.url): 0.0 for link in links}
        average_length = sum(map(len, documents)) / max(len(documents), 1)
        document_frequency = Counter(
            term for document in documents for term in set(document)
        )
        raw = [
            max(
                (
                    self._bm25(document, query, document_frequency, len(documents), average_length)
                    for query in queries
                ),
                default=0.0,
            )
            for document in documents
        ]
        maximum = max(raw, default=0.0)
        return {
            str(link.url): round(score / maximum, 6) if maximum > 0 else 0.0
            for link, score in zip(links, raw, strict=True)
        }

    @staticmethod
    def _document_tokens(link: DiscoveredLink) -> list[str]:
        path = unquote(urlparse(str(link.url)).path).replace("-", " ").replace("_", " ")
        return tokens(f"{link.title or ''} {path}")

    @staticmethod
    def _bm25(
        document: list[str],
        query: list[str],
        frequencies: Counter[str],
        document_count: int,
        average_length: float,
    ) -> float:
        counts = Counter(document)
        score = 0.0
        for term in set(query):
            term_frequency = counts[term]
            if not term_frequency:
                continue
            inverse_frequency = math.log(
                1 + (document_count - frequencies[term] + 0.5) / (frequencies[term] + 0.5)
            )
            denominator = term_frequency + 1.5 * (
                1 - 0.75 + 0.75 * len(document) / max(average_length, 1)
            )
            score += inverse_frequency * term_frequency * 2.5 / denominator
        return score
