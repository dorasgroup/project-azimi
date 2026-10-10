"""Composition roots for report execution, handoff and Monitoring finalization."""

import asyncio
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from app.core.config import settings

ReportSessions = async_sessionmaker(create_async_engine(settings.database_url, poolclass=NullPool), expire_on_commit=False, class_=AsyncSession)


def run_report_handoff(run_id: UUID, result_id: UUID, outbox_id: UUID | None) -> dict[str, object]:
    from app.modules.reports.infrastructure.generation_repository import SqlAlchemyReportGenerationRepository
    identifier = asyncio.run(SqlAlchemyReportGenerationRepository(ReportSessions).ensure_from_analysis_ready(run_id, result_id, outbox_id))
    return {"status": "ensured" if identifier else "cancelled", "report_generation_id": str(identifier) if identifier else None}


def run_report_generation(generation_id: UUID) -> dict[str, object]:
    from app.modules.reports.application.execute_generation import ExecuteReportGeneration
    from app.modules.reports.infrastructure.generation_repository import SqlAlchemyReportGenerationRepository
    from app.modules.reports.infrastructure.file_storage import LocalReportFileStorage
    from app.modules.reports.infrastructure.legacy_exporter import LegacyReportRenderer
    return asyncio.run(ExecuteReportGeneration(SqlAlchemyReportGenerationRepository(ReportSessions), LegacyReportRenderer(), LocalReportFileStorage()).execute(generation_id))


def run_report_finalization(generation_id: UUID, outbox_id: UUID | None) -> dict[str, object]:
    from app.modules.monitoring.application.finalize_report import FinalizeMonitoringReport
    from app.modules.monitoring.infrastructure.report_finalization_repository import MonitoringReportFinalizationRepository
    from app.modules.reports.infrastructure.terminal_reader import ReportTerminalReader
    return asyncio.run(FinalizeMonitoringReport(ReportTerminalReader(ReportSessions), MonitoringReportFinalizationRepository(ReportSessions)).execute(generation_id, outbox_id))
