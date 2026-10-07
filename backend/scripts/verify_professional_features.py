import os

from scripts.test_environment import assert_safe_test_environment

# Reject unsafe targets before importing application composition or fixtures.
assert_safe_test_environment(os.environ)

import asyncio
from datetime import date
from uuid import uuid4

import httpx
from sqlalchemy import delete, select

from app.core.security import create_access_token, hash_password
from app.db.session import SessionFactory, engine
from app.models import (
    AnalysisReport,
    BotIdentity,
    MonitoringJob,
    Source,
    SourceType,
    User,
    UserRole,
)
from app.models.enums import JobStatus


async def cleanup(marker: str) -> None:
    async with SessionFactory() as session:
        user = await session.scalar(select(User).where(User.username == marker))
        if user:
            await session.execute(
                delete(MonitoringJob).where(MonitoringJob.requested_by == user.id)
            )
            await session.execute(delete(Source).where(Source.created_by == user.id))
            await session.execute(delete(BotIdentity).where(BotIdentity.created_by == user.id))
            await session.delete(user)
        await session.commit()


async def create_fixture(marker: str) -> tuple[str, str]:
    async with SessionFactory() as session:
        admin = User(
            full_name="Professional Dashboard Admin",
            username=marker,
            email=f"{marker}@example.com",
            password_hash=hash_password("DashboardPass123"),
            role=UserRole.SUPER_ADMIN,
        )
        identity = BotIdentity(
            title="Professional Dashboard Identity",
            role_definition="Summarize dashboard fixture",
            creator=admin,
        )
        source = Source(
            title="Professional Dashboard Source",
            base_url=f"https://example.com/{marker}",
            type=SourceType.NEWS_SITE,
            creator=admin,
        )
        job = MonitoringJob(
            topic="Professional dashboard",
            date_from_jalali="1405/03/01",
            date_to_jalali="1405/03/02",
            date_from_gregorian=date(2026, 5, 22),
            date_to_gregorian=date(2026, 5, 23),
            status=JobStatus.COMPLETED,
            requester=admin,
            bot_identity=identity,
            sources=[source],
        )
        report = AnalysisReport(
            job=job,
            creator=admin,
            title="Professional dashboard report",
            summary="Dashboard summary fixture",
            full_report={"verified": True},
            sources_json=[{"url": source.base_url}],
        )
        session.add_all([admin, identity, source, job, report])
        await session.commit()
        access_token, _ = create_access_token(str(admin.id), admin.role)
        return access_token, str(job.id)


async def verify() -> None:
    marker = f"professional-{uuid4().hex[:12]}"
    await cleanup(marker)
    try:
        access_token, job_id = await create_fixture(marker)
        async with httpx.AsyncClient(base_url="http://localhost:8000/api/v1", timeout=30) as client:
            anonymous = await client.get("/dashboard/summary")
            assert anonymous.status_code == 401, anonymous.text

            response = await client.get(
                "/dashboard/summary",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["counts"]["monitoring_jobs"] >= 1
            assert body["counts"]["reports"] >= 1
            assert body["jobs_by_status"]["completed"] >= 1
            assert any(item["id"] == job_id for item in body["recent_jobs"])
            assert body["recent_reports"], body

        print("Professional feature verification passed: dashboard summary and access control")
    finally:
        await cleanup(marker)
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(verify())
