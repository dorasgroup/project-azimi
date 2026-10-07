"""Map validated report documents to analysis domain records."""

from typing import Protocol, Sequence

from app.modules.analysis.domain import (
    AnalysisExecutiveSummary,
    AnalysisReportPayload,
    AnalysisSourceReference,
)


class _Summary(Protocol):
    text: str


class _Source(Protocol):
    title: str
    url: object
    source_name: str


class ReportDocument(Protocol):
    title: str
    executive_summary: _Summary
    sources: Sequence[_Source]

    def model_dump(self, *, mode: str) -> dict[str, object]: ...


def report_payload(report: ReportDocument) -> AnalysisReportPayload:
    return AnalysisReportPayload(
        title=report.title,
        executive_summary=AnalysisExecutiveSummary(text=report.executive_summary.text),
        sources=tuple(
            AnalysisSourceReference(
                title=source.title,
                url=str(source.url),
                source_name=source.source_name,
            )
            for source in report.sources
        ),
        content=report.model_dump(mode="json"),
    )
