"""Real Celery prefork crash after first durable claim, in the isolated probe only."""

import os

from scripts.test_environment import assert_safe_test_environment

assert_safe_test_environment(os.environ)
if not os.environ["DATABASE_URL"].endswith("/phase8_recovery"):
    raise RuntimeError("Crash fixture requires the dedicated recovery database")

import asyncio
from datetime import UTC, datetime
from app.workers import report_tasks
from app.workers.celery_app import celery_app
from app.bootstrap.report_worker import ReportSessions
from app.modules.reports.application.execute_generation import ExecuteReportGeneration
from app.modules.reports.domain.generation import ReportClaimKind
from app.modules.reports.infrastructure.generation_repository import SqlAlchemyReportGenerationRepository
from app.modules.reports.infrastructure.legacy_exporter import LegacyReportRenderer
from app.modules.reports.infrastructure.file_storage import LocalReportFileStorage

class CrashRepository(SqlAlchemyReportGenerationRepository):
    async def claim(self, *args, **kwargs):
        claim = await super().claim(*args, **kwargs)
        if claim.kind == ReportClaimKind.CLAIMED and claim.attempt_count == 1:
            await self.renew_lease(args[0], claim.token, 15, datetime.now(UTC))
            os._exit(99)
        return claim


def run(identifier):
    return asyncio.run(ExecuteReportGeneration(CrashRepository(ReportSessions), LegacyReportRenderer(), LocalReportFileStorage(), lease_seconds=15, heartbeat_seconds=2).execute(identifier))


report_tasks.run_report_generation = run
celery_app.worker_main(["worker", "-Q", "report.generate", "-c", "1", "-Ofair", "--loglevel=INFO", "-n", "phase8-crash@%h", "--prefetch-multiplier=1"])
