"""Disposable Phase 8 recovery fixture. Never run against production databases."""

import os

from scripts.test_environment import assert_safe_test_environment

assert_safe_test_environment(os.environ)
if not os.environ["DATABASE_URL"].endswith("/phase8_recovery"):
    raise RuntimeError("Recovery probe requires the dedicated recovery database")

import asyncio
import json
import sys
from uuid import UUID
from sqlalchemy import select
from app.models.integration_outbox import IntegrationOutbox
from app.models.monitoring_run import MonitoringRun
from app.models.report_generation import OrmReportGeneration
from app.modules.reports.infrastructure.generation_repository import SqlAlchemyReportGenerationRepository
from tests.test_phase8_generation_repository import seed_ready, sessions


async def main():
    if sys.argv[1] == "seed":
        run_id, result_id = await seed_ready()
        generation_id = await SqlAlchemyReportGenerationRepository(sessions).ensure_from_analysis_ready(run_id, result_id, None)
        print(json.dumps({"run_id": str(run_id), "result_id": str(result_id), "generation_id": str(generation_id)}))
    elif sys.argv[1] == "status":
        identifier = UUID(sys.argv[2])
        async with sessions() as session:
            generation = await session.get(OrmReportGeneration, identifier)
            run = await session.get(MonitoringRun, generation.run_id)
            rows = (await session.scalars(select(IntegrationOutbox).where(IntegrationOutbox.message_key.in_([f"report-generation:{identifier}", f"report-terminal:{identifier}"])))).all()
            print(json.dumps({"status": generation.status, "attempt_count": generation.attempt_count,
                              "run_status": run.status.value,
                              "outbox": {r.message_type: {"status": r.status, "publish_count": r.attempt_count, "acked": r.consumer_acked_at is not None} for r in rows}}))
    else:
        raise ValueError("Unknown probe command")


asyncio.run(main())
