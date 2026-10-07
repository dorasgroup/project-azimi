"""Combine independent lexical, concept, provenance and temporal signals."""

from dataclasses import asdict
from datetime import date

from app.crawler.discovery import is_probable_content_url
from app.crawler.schemas import DiscoveredLink
from app.modules.crawling.domain.ranking import CandidateHybridScore, HybridRankingWeights
from app.modules.crawling.infrastructure.bm25_lexical_scorer import Bm25LexicalScorer
from app.processing.query_planner import query_plan

PROVIDER_CONFIDENCE = {
    "rss": 1.0,
    "sitemap": 0.9,
    "search": 0.75,
    "archive": 0.7,
    "gdelt": 0.65,
    "common_crawl": 0.55,
    "html": 0.45,
}


class HybridCandidateScorer:
    """Score each candidate without network or model calls."""

    def __init__(
        self,
        weights: HybridRankingWeights | None = None,
        lexical: Bm25LexicalScorer | None = None,
    ) -> None:
        self.weights = weights or HybridRankingWeights()
        self._lexical = lexical or Bm25LexicalScorer()

    def score(
        self,
        links: list[DiscoveredLink],
        topic: str,
        *,
        date_from: date | None,
        date_to: date | None,
    ) -> dict[str, CandidateHybridScore]:
        lexical_scores = self._lexical.score(links, topic)
        concept_count = max(len(query_plan(topic).concepts), 1)
        return {
            str(link.url): self._combine(
                link,
                lexical_scores[str(link.url)],
                min(len(set(link.topic_matches)) / concept_count, 1.0),
                date_from,
                date_to,
            )
            for link in links
        }

    def weights_dict(self) -> dict[str, float]:
        return asdict(self.weights)

    def _combine(
        self,
        link: DiscoveredLink,
        lexical: float,
        semantic: float,
        date_from: date | None,
        date_to: date | None,
    ) -> CandidateHybridScore:
        methods = set(link.discovery_methods or [link.discovered_via])
        provider = max((PROVIDER_CONFIDENCE.get(method, 0.3) for method in methods), default=0.3)
        provenance = min(len(methods) / 3, 1.0)
        evidence = 0.7 * provider + 0.3 * provenance
        if link.published_at is not None:
            temporal = 1.0
        elif date_from is not None or date_to is not None:
            temporal = 0.25
        else:
            temporal = 0.5
        structure = (
            (0.5 if link.title else 0.0)
            + (0.5 if is_probable_content_url(str(link.url)) else 0.0)
        )
        total = (
            self.weights.lexical * lexical
            + self.weights.semantic * semantic
            + self.weights.evidence * evidence
            + self.weights.temporal * temporal
            + self.weights.structure * structure
        )
        return CandidateHybridScore(
            lexical=round(lexical, 6),
            semantic=round(semantic, 6),
            evidence=round(evidence, 6),
            temporal=round(temporal, 6),
            structure=round(structure, 6),
            total=round(total, 6),
        )
