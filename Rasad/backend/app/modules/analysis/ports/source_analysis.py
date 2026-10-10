"""Typed boundary ports for per-source analysis."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, Sequence
from uuid import UUID

from app.modules.analysis.domain.source_analysis import (
    SOURCE_ANALYSIS_LEASE_SECONDS,
    AnalysisClaimOutcome,
    SourceAnalysis,
    SourceAnalysisResultData,
    SourceAnalysisSpec,
)


class SourceTaskDocumentRecord(Protocol):
    id: UUID
    source_task_id: UUID
    run_id: UUID
    source_id: UUID
    url: str
    canonical_url: str | None
    title: str | None
    clean_text: str
    published_at: datetime | None
    language: str | None
    matched_score: float | None
    content_hash: str | None


class SourceTaskDocumentReaderPort(Protocol):
    async def get_documents_for_task(
        self, source_task_id: UUID
    ) -> Sequence[SourceTaskDocumentRecord]: ...


class SourceAnalysisRepositoryPort(Protocol):
    async def get_by_id(self, analysis_id: UUID) -> SourceAnalysis | None: ...

    async def get_by_source_task_id(self, task_id: UUID) -> SourceAnalysis | None: ...

    async def ensure_for_source_task(
        self,
        source_task_id: UUID,
        run_id: UUID,
        source_id: UUID,
        spec: SourceAnalysisSpec,
    ) -> SourceAnalysis: ...

    async def claim_explicit(
        self,
        analysis_id: UUID,
        worker_token: UUID | None = None,
        lease_seconds: int = SOURCE_ANALYSIS_LEASE_SECONDS,
    ) -> AnalysisClaimOutcome: ...

    async def renew_lease(
        self,
        analysis_id: UUID,
        claim_token: UUID,
        extension_seconds: int = SOURCE_ANALYSIS_LEASE_SECONDS,
    ) -> bool: ...

    async def release_claim(
        self,
        analysis_id: UUID,
        claim_token: UUID,
    ) -> bool: ...

    async def complete_analysis(
        self,
        analysis_id: UUID,
        claim_token: UUID,
        input_digest: str | None = None,
    ) -> bool: ...

    async def fail_analysis(
        self,
        analysis_id: UUID,
        claim_token: UUID,
        error_code: str,
        error_message: str,
    ) -> bool: ...


class SourceAnalysisResultRepositoryPort(Protocol):
    async def save_result_fenced(
        self,
        analysis_id: UUID,
        claim_token: UUID,
        result_data: SourceAnalysisResultData,
    ) -> bool: ...

    async def get_by_analysis_id(
        self, analysis_id: UUID
    ) -> SourceAnalysisResultData | None: ...


class SourceAnalyzerPort(Protocol):
    async def analyze_source(
        self,
        spec: SourceAnalysisSpec,
        documents: Sequence[SourceTaskDocumentRecord],
        topic: str,
    ) -> SourceAnalysisResultData: ...


class SourceAnalysisDispatcherPort(Protocol):
    def dispatch_analysis_task(self, source_analysis_id: UUID) -> str: ...
