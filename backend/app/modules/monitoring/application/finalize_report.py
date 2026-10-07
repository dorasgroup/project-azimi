"""Monitoring owns the terminal transition after Reporting's durable outcome."""

from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID
from app.shared.integration.report_terminal import ReportTerminalSnapshot


class ReportTerminalView(Protocol):
    async def read(self, generation_id: UUID) -> ReportTerminalSnapshot: ...


class MonitoringReportFinalization(Protocol):
    async def finalize(self, report: ReportTerminalSnapshot, outbox_id: UUID | None, now: datetime) -> str: ...


class FinalizeMonitoringReport:
    def __init__(self, reader: ReportTerminalView, repository: MonitoringReportFinalization) -> None:
        self._reader, self._repository = reader, repository

    async def execute(self, generation_id: UUID, outbox_id: UUID | None) -> dict[str, object]:
        report = await self._reader.read(generation_id)
        if report.status == "completed" and not report.required_artifacts_durable:
            raise ValueError("Completed report lacks its required durable artifacts")
        status = await self._repository.finalize(report, outbox_id, datetime.now(UTC))
        return {"status": status, "run_id": str(report.run_id)}
