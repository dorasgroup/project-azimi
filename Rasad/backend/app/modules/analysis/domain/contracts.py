"""Framework-independent contracts for cognitive analysis."""

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class AnalysisExecutiveSummary:
    text: str


@dataclass(frozen=True)
class AnalysisSourceReference:
    title: str
    url: str
    source_name: str


@dataclass(frozen=True)
class AnalysisReportPayload:
    title: str
    executive_summary: AnalysisExecutiveSummary
    sources: tuple[AnalysisSourceReference, ...]
    content: Mapping[str, object]


@dataclass(frozen=True)
class AnalysisRequest:
    inputs: Mapping[str, object]
    user_id: str


@dataclass(frozen=True)
class AnalysisOutcome:
    report: AnalysisReportPayload
