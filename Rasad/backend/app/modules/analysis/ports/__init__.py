"""Typed analysis boundary contracts."""

from datetime import date, datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from app.modules.analysis.domain import (
    AnalysisOutcome,
    AnalysisRequest,
    ModelGenerationRequest,
    ModelGenerationResult,
)
from app.modules.analysis.ports.source_analysis import (
    SourceAnalysisDispatcherPort,
    SourceAnalysisRepositoryPort,
    SourceAnalysisResultRepositoryPort,
    SourceAnalyzerPort,
    SourceTaskDocumentReaderPort,
    SourceTaskDocumentRecord,
)


class AnalysisDocumentRecord(Protocol):
    source_id: UUID
    title: str | None
    url: str
    canonical_url: str | None
    clean_text: str
    language: str | None
    published_at: datetime | None
    matched_score: float | None


class AnalysisJobRecord(Protocol):
    id: UUID
    topic: str
    description: str | None
    date_from_gregorian: date
    date_to_gregorian: date
    date_from_jalali: str
    date_to_jalali: str
    requested_by: UUID
    documents: list[AnalysisDocumentRecord]


class AnalysisGateway(Protocol):
    async def analyze(self, request: AnalysisRequest) -> AnalysisOutcome: ...


class ModelGateway(Protocol):
    async def generate_json(self, request: ModelGenerationRequest) -> ModelGenerationResult: ...


@runtime_checkable
class AnalysisJobReader(Protocol):
    async def get_for_analysis(self, job_id: UUID) -> AnalysisJobRecord | None: ...


class AnalysisReportRecord(Protocol):
    id: UUID


class AnalysisReportWriter(Protocol):
    async def save(self, report: AnalysisReportRecord) -> None: ...

    async def flush(self) -> None: ...


__all__ = [
    "AnalysisDocumentRecord",
    "AnalysisGateway",
    "AnalysisJobReader",
    "AnalysisJobRecord",
    "AnalysisReportRecord",
    "AnalysisReportWriter",
    "ModelGateway",
    "SourceAnalysisDispatcherPort",
    "SourceAnalysisRepositoryPort",
    "SourceAnalysisResultRepositoryPort",
    "SourceAnalyzerPort",
    "SourceTaskDocumentReaderPort",
    "SourceTaskDocumentRecord",
]
