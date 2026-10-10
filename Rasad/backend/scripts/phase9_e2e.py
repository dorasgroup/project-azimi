"""Dedicated real broker/API/export acceptance. Only external crawl/model mocked.

Never loaded by normal app/worker entrypoints; explicit isolated DB is mandatory.
"""

import os
from scripts.test_environment import assert_safe_test_environment
assert_safe_test_environment(os.environ)
if not os.environ["DATABASE_URL"].endswith("/phase9_e2e"):
    raise RuntimeError("Phase9 E2E requires its exact disposable database")

import asyncio
import json
import re
import sys
import time
from types import SimpleNamespace
from uuid import UUID, uuid4
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from app.core.config import settings
from app.models import User, BotIdentity, Source, SourceType, MonitoringRun, MonitoringJob, AnalysisReport
from app.models.integration_outbox import IntegrationOutbox
from app.models.report_generation import OrmReportArtifact, OrmReportGeneration
from app.core.security import hash_password
from app.modules.platform_settings.domain import ModelProviderRuntime

sessions = async_sessionmaker(create_async_engine(settings.database_url, poolclass=NullPool), expire_on_commit=False)


class SyntheticGateway:
    async def generate_json(self, request):
        # Only synthetic fixture text is allowed in this acceptance environment.
        if "evidence_map" in request.system_prompt:
            urls = re.findall(r'"url":\s*"([^"\n]+)"', request.user_prompt)
            content = {"summary":"تحلیل شناختی دادهٔ مصنوعی مذاکرات", "findings":[
                {"title":"یافتهٔ مصنوعی", "explanation":"تحلیل آزمایشی بدون ادعای واقعی", "evidence":"متن آزمایشی", "document_url":urls[0]}
            ], "evidence_map":{u:"شاهد مصنوعی" for u in urls},
                "cognitive_analysis":{"sub_sections":[{"title":"روایت", "content":"مقایسهٔ دادهٔ مصنوعی"}], "key_findings":["یافتهٔ مصنوعی"]},
                "future_analysis":{"scenarios":[], "key_variables":[]}, "coverage_notes":["آزمون مصنوعی"]}
        else:
            content = {"title":"گزارش آزمایشی", "executive_summary":{"text":"جمع‌بندی شناختی مصنوعی", "metrics":[]},
                "timeline":[], "sources_analysis":[], "cognitive_analysis":{"sub_sections":[{"title":"تحلیل", "content":"مقایسهٔ مصنوعی"}], "key_findings":["نتیجهٔ مصنوعی"]},
                "future_analysis":{"scenarios":[], "key_variables":[]}, "conclusion":"نتیجهٔ آزمایشی", "sources":[]}
        return SimpleNamespace(content=content)


def install_external_fixtures():
    from app.bootstrap import analysis
    from app.modules.analysis.infrastructure import spec_resolver
    spec_resolver.get_active_model_provider = lambda: ModelProviderRuntime(
        provider_id="gapgpt", model="synthetic-pinned-model", base_url="https://example.invalid/v1", api_key="synthetic-not-a-secret")
    analysis.provide_frozen_model_gateway = lambda spec: SyntheticGateway()
    # Use the already characterized fake external engine, not fake persistence.
    sys.path.insert(0, "/app/tests")
    from test_phase6a_contract_closure import FakeControlledEngine
    from app.modules.monitoring.infrastructure import source_execution_engine
    source_execution_engine.SourceExecutionEngine = lambda: FakeControlledEngine(["مذاکرات مصنوعی", "شاهد مصنوعی"])


async def seed():
    suffix = uuid4().hex
    async with sessions() as s, s.begin():
        user = User(full_name="Synthetic E2E owner", username="e2e-"+suffix,
                    email=f"{suffix}@example.invalid", password_hash=hash_password("synthetic-phase9-only"), is_active=True)
        other = User(full_name="Synthetic other owner", username="other-"+suffix,
                     email=f"other-{suffix}@example.invalid", password_hash=hash_password("synthetic-phase9-only"), is_active=True)
        s.add_all([user, other])
        await s.flush()
        bot = BotIdentity(title="Synthetic identity", role_definition="تحلیل دادهٔ مصنوعی", created_by=user.id)
        sources = [Source(title=f"Synthetic source {i}", base_url=f"https://{suffix}-{i}.example.invalid", type=SourceType.NEWS_SITE, created_by=user.id) for i in (1,2)]
        s.add_all([bot,*sources])
        await s.flush()
        return user.username, other.username, str(bot.id), [str(s.id) for s in sources]


async def audit(job_id):
    async with sessions() as s:
        run = await s.scalar(select(MonitoringRun).where(MonitoringRun.monitoring_job_id==UUID(job_id)))
        assert run and run.execution_mode == "distributed" and run.status.value == "completed"
        assert run.terminal_sources == run.expected_sources == 2
        generation = await s.scalar(select(OrmReportGeneration).where(OrmReportGeneration.run_id==run.id))
        assert generation.status == "completed"
        artifacts = list((await s.scalars(select(OrmReportArtifact).where(OrmReportArtifact.report_generation_id==generation.id))).all())
        assert {a.format for a in artifacts} == {"pdf", "docx"}
        assert all(a.content_size > 0 and len(a.content_checksum)==64 for a in artifacts)
        assert await s.scalar(select(func.count()).select_from(AnalysisReport).where(AnalysisReport.job_id==UUID(job_id))) == 1
        messages = list((await s.scalars(select(IntegrationOutbox))).all())
        assert messages and all(m.consumer_acked_at is not None for m in messages if m.message_type != "DEADLINE_REACHED")
        return {"run_id":str(run.id), "generation_id":str(generation.id),
                "artifact_sizes":{a.format:a.content_size for a in artifacts},
                "message_types":sorted({m.message_type for m in messages})}


def probe():
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db.session import engine
    username, othername, bot_id, source_ids = asyncio.run(seed())
    asyncio.run(engine.dispose())
    with TestClient(app) as client:
        def login(name):
            response = client.post("/api/v1/auth/login", json={"login":name, "password":"synthetic-phase9-only"})
            assert response.status_code == 200, response.status_code
            return {"Authorization":"Bearer "+response.json()["access_token"]}
        auth, other = login(username), login(othername)
        response = client.post("/api/v1/monitoring/jobs", headers=auth, json={
            "topic":"مذاکرات مصنوعی", "description":"صرفاً آزمون ایزوله", "date_from_jalali":"1404/10/10", "date_to_jalali":"1404/10/15",
            "bot_identity_id":bot_id, "source_ids":source_ids})
        assert response.status_code == 202, response.text[:200]
        jid = response.json()["id"]
        assert client.get(f"/api/v1/monitoring/jobs/{jid}", headers=other).status_code == 404
        stages = set()
        deadline = time.monotonic()+120
        while time.monotonic()<deadline:
            current = client.get(f"/api/v1/monitoring/jobs/{jid}", headers=auth)
            assert current.status_code == 200
            body = current.json()
            if body.get("progress"):
                stages.add(body["progress"]["stage"])
                assert body["progress"]["eta_seconds"] is None
            if body["status"] in {"completed", "failed", "cancelled"}:
                break
            time.sleep(1)
        assert body["status"] == "completed", {"status":body["status"], "error":body["error_message"]}
        reports = client.get("/api/v1/reports", headers=auth).json()["items"]
        report = next(r for r in reports if r["job_id"] == jid)
        rid = report["id"]
        for fmt in ("pdf", "docx"):
            artifact = client.get(f"/api/v1/reports/{rid}/download/{fmt}", headers=auth)
            assert artifact.status_code == 200 and len(artifact.content)>100
            assert artifact.content.startswith(b"%PDF" if fmt=="pdf" else b"PK")
            assert client.get(f"/api/v1/reports/{rid}/download/{fmt}", headers=other).status_code == 404
        assert client.post(f"/api/v1/monitoring/jobs/{jid}/cancel", headers=auth).status_code == 200
        assert client.get(f"/api/v1/monitoring/jobs/{jid}", headers=auth).json()["status"] == "completed"
    evidence = asyncio.run(audit(jid))
    print(json.dumps({"status":"passed", "job_id":jid, "stages_observed":sorted(stages),
                      "external_crawl_and_model":"synthetic", "auth_and_exports":"real", **evidence}, ensure_ascii=False))


if __name__ == "__main__":
    if sys.argv[1] == "worker":
        install_external_fixtures()
        from app.workers.celery_app import celery_app
        celery_app.worker_main(["worker", "-Q", "celery,monitoring.source,analysis.source,analysis.aggregate,monitoring.control,report.generate",
                               "-c", "3", "--loglevel=WARNING", "-n", "phase9-e2e@%h"])
    elif sys.argv[1] == "probe":
        probe()
    else:
        raise ValueError("Explicit worker/probe role required")
