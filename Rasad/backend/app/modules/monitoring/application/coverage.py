"""Source coverage calculation and warning logger."""

from typing import Protocol
from uuid import UUID


class CoverageLoggerPort(Protocol):
    async def log_partial_coverage(
        self,
        job_id: UUID,
        missing_titles: list[str],
        covered_titles: list[str],
    ) -> None: ...


class DocumentCountsPort(Protocol):
    async def source_counts(self, job_id: UUID) -> dict[UUID, int]: ...


class CoverageRecorder:
    """Calculate document coverage across sources and warn if partial."""

    def __init__(
        self,
        document_counts: DocumentCountsPort,
        coverage_logger: CoverageLoggerPort,
    ) -> None:
        self._document_counts = document_counts
        self._coverage_logger = coverage_logger

    async def calculate_and_record(
        self, job_id: UUID, sources: list[dict[str, object]]
    ) -> int:
        counts = await self._document_counts.source_counts(job_id)
        missing: list[str] = []
        covered: list[str] = []

        for source in sources:
            s_id = source["id"]
            s_title = str(source.get("title", s_id))
            if counts.get(s_id, 0) == 0:
                missing.append(s_title)
            else:
                covered.append(s_title)

        if missing:
            await self._coverage_logger.log_partial_coverage(
                job_id, missing, covered
            )

        return sum(counts.values())
