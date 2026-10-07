"""Behavior-preserving orchestration for one source crawl."""

from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime

from app.crawler.schemas import CrawlBatchResult, CrawlPolicy, CrawledPage, DiscoveredLink, SourceProbe
from app.modules.crawling.infrastructure.crawl_diagnostics import CrawlDiagnosticsBuilder
from app.modules.crawling.infrastructure.candidate_ranker import CandidateRanker
from app.modules.crawling.infrastructure.candidate_ranker import ranking_trace
from app.modules.crawling.ports.checkpoint import CrawlCheckpointPort


ProbeSource = Callable[[str], Awaitable[SourceProbe]]
DiscoverLinks = Callable[[str, str, date | None, date | None], Awaitable[list[DiscoveredLink]]]
FetchPages = Callable[[str, list[DiscoveredLink]], Awaitable[list[CrawledPage]]]


def selection_trace(orchestrator: "CrawlOrchestrator") -> dict[str, object]:
    """Return the latest selection explanation without expanding the class API."""
    return ranking_trace(orchestrator._ranker)


class CrawlOrchestrator:
    """Coordinate probing, discovery, selection and fetching for one source."""

    def __init__(self, policy: CrawlPolicy, checkpoint: CrawlCheckpointPort | None = None) -> None:
        self._policy = policy
        self._ranker = CandidateRanker(policy)
        self._checkpoint = checkpoint

    def select_links(
        self,
        links: list[DiscoveredLink],
        *,
        date_from: date | None,
        date_to: date | None,
        topic: str | None = None,
    ) -> list[DiscoveredLink]:
        return self._ranker.rank(
            links,
            date_from=date_from,
            date_to=date_to,
            topic=topic,
        )

    async def crawl(
        self,
        source_url: str,
        topic: str,
        date_from: date | None,
        date_to: date | None,
        *,
        probe: ProbeSource,
        discover: DiscoverLinks,
        fetch_pages: FetchPages,
    ) -> CrawlBatchResult:
        started_at = datetime.now(UTC)
        probe_result = await probe(source_url)
        if not probe_result.success or probe_result.robots_allowed is False:
            finished_at = datetime.now(UTC)
            # Preserve already acquired evidence on a later probe failure. No
            # network work is performed and the failed/blocked gate stays visible.
            records = await self._checkpoint.load() if self._checkpoint else {}
            plan = records.get('plan') or {}
            selected = [DiscoveredLink.model_validate(item) for item in plan.get('selected', [])]
            discovered = [DiscoveredLink.model_validate(item) for item in plan.get('discovered', [])]
            pages = []
            for link in selected:
                cached = records.get('page:' + str(link.url))
                page = CrawledPage.model_validate(cached) if cached else CrawledPage(url=link.url, success=False,
                    metadata={'failure_kind': 'pending', 'retryable': True})
                pages.append(page)
            diagnostics = CrawlDiagnosticsBuilder.build(
                probe=probe_result, discovered=discovered, selected=selected, pages=pages,
                date_from=date_from, date_to=date_to, max_pages=self._policy.max_pages,
                started_at=started_at, finished_at=finished_at,
            )
            diagnostics.status = 'blocked' if probe_result.robots_allowed is False else 'partial' if any(p.success for p in pages) else 'failed'
            diagnostics.reason_code = 'robots_blocked' if probe_result.robots_allowed is False else 'probe_failed'
            return CrawlBatchResult(
                source_url=source_url,
                probe=probe_result,
                discovered_links=selected,
                pages=pages,
                started_at=started_at,
                finished_at=finished_at,
                diagnostics=diagnostics,
            )

        effective_source_url = str(probe_result.final_url or source_url)
        records = await self._checkpoint.load() if self._checkpoint else {}
        plan = records.get('plan')
        if plan and plan.get('source_url') == effective_source_url:
            discovered = [DiscoveredLink.model_validate(item) for item in plan['discovered']]
            selected = [DiscoveredLink.model_validate(item) for item in plan['selected']]
        else:
            discovered = await discover(effective_source_url, topic, date_from, date_to)
            selected = self.select_links(discovered, date_from=date_from, date_to=date_to, topic=topic)
            if self._checkpoint:
                await self._checkpoint.save('plan', {'source_url': effective_source_url,
                    'discovered': [item.model_dump(mode='json') for item in discovered],
                    'selected': [item.model_dump(mode='json') for item in selected]})
        pages = await fetch_pages(effective_source_url, selected)
        finished_at = datetime.now(UTC)
        return CrawlBatchResult(
            source_url=source_url,
            probe=probe_result,
            discovered_links=selected,
            pages=pages,
            started_at=started_at,
            finished_at=finished_at,
            diagnostics=CrawlDiagnosticsBuilder.build(
                probe=probe_result,
                discovered=discovered,
                selected=selected,
                pages=pages,
                date_from=date_from,
                date_to=date_to,
                max_pages=self._policy.max_pages,
                started_at=started_at,
                finished_at=finished_at,
            ),
        )
