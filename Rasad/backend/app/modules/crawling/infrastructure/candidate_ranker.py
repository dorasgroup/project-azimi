"""Deterministic ranking and provider-diverse allocation of crawl candidates."""

import logging
import os
from collections import Counter
from datetime import date

from app.crawler.discovery import is_probable_content_url
from app.crawler.schemas import CrawlPolicy, DiscoveredLink
from app.modules.crawling.domain.ranking import CandidateHybridScore, RankingTrace
from app.modules.crawling.infrastructure.hybrid_candidate_scorer import HybridCandidateScorer


PROVIDER_WEIGHTS = {
    "rss": 40,
    "sitemap": 35,
    "search": 30,
    "archive": 25,
    "gdelt": 22,
    "common_crawl": 18,
    "html": 15,
}
PROVIDER_ORDER = tuple(PROVIDER_WEIGHTS)
logger = logging.getLogger(__name__)


def ranking_trace(ranker: "CandidateRanker") -> dict[str, object]:
    """Return a serialization-safe explanation of the latest ranking."""
    return ranker._last_trace.as_dict()


def _hybrid_ranking_enabled() -> bool:
    value = os.getenv("CRAWL_HYBRID_RANKING_ENABLED", "true").strip().casefold()
    return value not in {"0", "false", "no", "off"}


class CandidateRanker:
    """Filter by date, rank by evidence and preserve provider diversity."""

    def __init__(
        self,
        policy: CrawlPolicy,
        scorer: HybridCandidateScorer | None = None,
        hybrid_enabled: bool | None = None,
    ) -> None:
        self._policy = policy
        self._scorer = scorer or HybridCandidateScorer()
        self._hybrid_enabled = (
            _hybrid_ranking_enabled() if hybrid_enabled is None else hybrid_enabled
        )
        self._last_trace = RankingTrace(enabled=False)

    def rank(
        self,
        links: list[DiscoveredLink],
        *,
        date_from: date | None,
        date_to: date | None,
        topic: str | None = None,
    ) -> list[DiscoveredLink]:
        eligible = [
            link
            for link in links
            if link.published_at is None
            or (
                (date_from is None or link.published_at.date() >= date_from)
                and (date_to is None or link.published_at.date() <= date_to)
            )
        ]
        if not eligible:
            self._last_trace = RankingTrace(
                enabled=bool(self._hybrid_enabled and topic),
                candidate_count=len(links),
            )
            return []
        hybrid_active = bool(self._hybrid_enabled and topic)
        scores = (
            self._scorer.score(
                eligible,
                topic or "",
                date_from=date_from,
                date_to=date_to,
            )
            if hybrid_active
            else {}
        )
        ordered = sorted(eligible, key=lambda link: self._sort_key(link, scores))
        if not self._policy.candidate_diversity_enabled:
            selected = ordered[: self._policy.max_pages]
            self._record_trace(links, ordered, selected, scores, hybrid_active)
            return selected

        topical = [link for link in ordered if link.topic_matches]
        diversity_pool = topical or ordered
        selected: list[DiscoveredLink] = []
        selected_urls: set[str] = set()
        # Reserve representation across a broad time window, using only dated
        # candidates with a topical signal. Never invent dates or admit old URLs.
        if date_from and date_to and (date_to - date_from).days >= 31:
            buckets = {}
            width = max(7, ((date_to - date_from).days + 1) // 8)
            for link in ordered:
                score = scores.get(str(link.url))
                topical_signal = bool(link.topic_matches or (score and score.lexical > 0))
                if link.published_at and topical_signal:
                    bucket = (link.published_at.date() - date_from).days // width
                    buckets.setdefault(bucket, link)
            for bucket in sorted(buckets):
                if len(selected) >= min(8, self._policy.max_pages):
                    break
                selected.append(buckets[bucket])
                selected_urls.add(str(buckets[bucket].url))
        providers = [
            provider
            for provider in PROVIDER_ORDER
            if any(self._primary_provider(link) == provider for link in diversity_pool)
        ]
        if scores:
            providers.sort(
                key=lambda provider: (
                    -max(
                        self._ranking_value(link, scores)
                        for link in diversity_pool
                        if self._primary_provider(link) == provider
                    ),
                    PROVIDER_ORDER.index(provider),
                )
            )
        for provider in providers[: self._policy.candidate_provider_slots]:
            candidate = next((
                link
                for link in diversity_pool
                if self._primary_provider(link) == provider
                and str(link.url) not in selected_urls
            ), None)
            if candidate is None or len(selected) >= self._policy.max_pages:
                continue
            selected.append(candidate)
            selected_urls.add(str(candidate.url))
            if len(selected) >= self._policy.max_pages:
                self._record_trace(links, ordered, selected, scores, hybrid_active)
                return selected

        provider_usage = Counter(self._primary_provider(link) for link in selected)
        remaining = [link for link in ordered if str(link.url) not in selected_urls]
        while remaining and len(selected) < self._policy.max_pages:
            candidate = min(
                remaining,
                key=lambda link: (
                    -(
                        self._ranking_value(link, scores)
                        - provider_usage[self._primary_provider(link)]
                        * self._policy.candidate_repeat_provider_penalty
                    ),
                    str(link.url),
                ),
            )
            remaining.remove(candidate)
            selected.append(candidate)
            selected_urls.add(str(candidate.url))
            provider_usage[self._primary_provider(candidate)] += 1
        self._record_trace(links, ordered, selected, scores, hybrid_active)
        return selected

    @staticmethod
    def _primary_provider(link: DiscoveredLink) -> str:
        methods = link.discovery_methods or [link.discovered_via]
        return min(
            methods,
            key=lambda method: (
                PROVIDER_ORDER.index(method) if method in PROVIDER_ORDER else len(PROVIDER_ORDER),
                method,
            ),
        )

    @staticmethod
    def _score(link: DiscoveredLink) -> int:
        methods = link.discovery_methods or [link.discovered_via]
        provider_score = max((PROVIDER_WEIGHTS.get(method, 0) for method in methods), default=0)
        return (
            len(link.topic_matches) * 100
            + (35 if link.published_at is not None else 0)
            + max(0, len(set(methods)) - 1) * 15
            + provider_score
            + (5 if link.title else 0)
            + (5 if is_probable_content_url(str(link.url)) else 0)
        )

    @classmethod
    def _sort_key(
        cls,
        link: DiscoveredLink,
        scores: dict[str, CandidateHybridScore] | None = None,
    ) -> tuple[float, int, str]:
        timestamp = int(link.published_at.timestamp()) if link.published_at else 0
        return -cls._ranking_value(link, scores or {}), -timestamp, str(link.url)

    @classmethod
    def _ranking_value(
        cls,
        link: DiscoveredLink,
        scores: dict[str, CandidateHybridScore],
    ) -> float:
        score = scores.get(str(link.url))
        return score.total * 1000 if score is not None else float(cls._score(link))

    def _record_trace(
        self,
        candidates: list[DiscoveredLink],
        ordered: list[DiscoveredLink],
        selected: list[DiscoveredLink],
        scores: dict[str, CandidateHybridScore],
        enabled: bool,
    ) -> None:
        selected_urls = {str(link.url) for link in selected}
        self._last_trace = RankingTrace(
            enabled=enabled,
            candidate_count=len(candidates),
            eligible_count=len(ordered),
            selected_count=len(selected),
            weights=self._scorer.weights_dict() if enabled else {},
            top_candidates=[
                {
                    "url": str(link.url),
                    "selected": str(link.url) in selected_urls,
                    "provider": self._primary_provider(link),
                    "score": (
                        scores[str(link.url)].as_dict()
                        if str(link.url) in scores
                        else {"legacy": self._score(link)}
                    ),
                }
                for link in ordered[:20]
            ],
        )
        logger.info("crawl_candidate_ranking", extra={"ranking_trace": self._last_trace.as_dict()})
