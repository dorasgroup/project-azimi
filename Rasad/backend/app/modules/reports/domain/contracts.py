"""Framework-independent report export contracts."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping, Protocol, Sequence
from uuid import UUID


class ReportExportFormat(StrEnum):
    PDF = "pdf"
    DOCX = "docx"


class RenderableReport(Protocol):
    id: UUID
    title: str
    summary: str | None
    full_report: Mapping[str, object]
    sources_json: Sequence[Mapping[str, object]]


@dataclass(frozen=True)
class ReportExportRequest:
    report: RenderableReport
    export_format: ReportExportFormat


@dataclass(frozen=True)
class RenderedReport:
    content: bytes
    filename: str


@dataclass(frozen=True)
class ReportAccessContext:
    user_id: UUID | None
    role: str | None
