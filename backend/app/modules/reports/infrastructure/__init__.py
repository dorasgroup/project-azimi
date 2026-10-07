"""Report database and file-storage adapters."""

from app.modules.reports.infrastructure.file_storage import LocalReportFileStorage

__all__ = [
    "LegacyReportExporter",
    "LegacyReportRenderer",
    "LocalReportFileStorage",
    "ReportExportBatchAdapter",
    "SqlAlchemyReportRepository",
]


def __getattr__(name: str):
    if name in {"LegacyReportExporter", "LegacyReportRenderer", "ReportExportBatchAdapter"}:
        from app.modules.reports.infrastructure.legacy_exporter import (
            LegacyReportExporter,
            LegacyReportRenderer,
            ReportExportBatchAdapter,
        )

        return {
            "LegacyReportExporter": LegacyReportExporter,
            "LegacyReportRenderer": LegacyReportRenderer,
            "ReportExportBatchAdapter": ReportExportBatchAdapter,
        }[name]
    if name == "SqlAlchemyReportRepository":
        from app.modules.reports.infrastructure.repository import SqlAlchemyReportRepository

        return SqlAlchemyReportRepository
    raise AttributeError(name)
