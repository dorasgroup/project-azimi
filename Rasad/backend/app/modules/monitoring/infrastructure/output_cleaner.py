"""Cleanup adapter composed from Documents and Reports ports."""

from app.modules.documents.ports import DocumentRepository
from app.modules.monitoring.ports import MonitoringJobRecord
from app.modules.reports.ports import ReportFileStorage, ReportRepository


class MonitoringOutputCleaner:
    """Remove persisted artifacts owned by a single monitoring job."""

    def __init__(
        self,
        documents: DocumentRepository,
        reports: ReportRepository,
        files: ReportFileStorage,
    ) -> None:
        self._documents = documents
        self._reports = reports
        self._files = files

    async def clear(self, job: MonitoringJobRecord) -> None:
        await self.clear_files(job)
        await self._documents.clear_for_job(job.id)
        await self._reports.delete_for_job(job.id)

    async def clear_files(self, job: MonitoringJobRecord) -> None:
        if job.report is None:
            return
        for path in (job.report.pdf_path, job.report.docx_path):
            if path:
                self._files.delete(path)
