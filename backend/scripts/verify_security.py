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
    RefreshToken,
    Source,
    SourceType,
    User,
    UserRole,
)


async def cleanup(marker: str) -> None:
    async with SessionFactory() as session:
        users = list(
            (
                await session.scalars(
                    select(User).where(User.username.in_([f"{marker}-admin", f"{marker}-user"]))
                )
            ).all()
        )
        user_ids = [user.id for user in users]
        if user_ids:
            await session.execute(
                delete(AnalysisReport).where(AnalysisReport.created_by.in_(user_ids))
            )
            await session.execute(delete(RefreshToken).where(RefreshToken.user_id.in_(user_ids)))
            await session.execute(
                delete(MonitoringJob).where(MonitoringJob.requested_by.in_(user_ids))
            )
            await session.execute(delete(BotIdentity).where(BotIdentity.created_by.in_(user_ids)))
            await session.execute(delete(Source).where(Source.created_by.in_(user_ids)))
        for user in users:
            await session.delete(user)
        await session.commit()


async def create_fixture(marker: str) -> tuple[str, str, str, str, str]:
    async with SessionFactory() as session:
        admin = User(
            full_name="Security Admin",
            username=f"{marker}-admin",
            email=f"{marker}-admin@example.com",
            password_hash=hash_password("AdminPass123"),
            role=UserRole.SUPER_ADMIN,
        )
        user = User(
            full_name="Security User",
            username=f"{marker}-user",
            email=f"{marker}-user@example.com",
            password_hash=hash_password("UserPass123"),
            role=UserRole.USER,
        )
        identity = BotIdentity(
            title="Security identity",
            role_definition="Security verification",
            creator=admin,
        )
        source = Source(
            title="Security source",
            base_url=f"https://{marker}.example.test",
            type=SourceType.NEWS_SITE,
            creator=admin,
        )
        admin_job = MonitoringJob(
            topic="Admin job",
            date_from_jalali="1405/03/01",
            date_to_jalali="1405/03/17",
            date_from_gregorian=date(2026, 5, 22),
            date_to_gregorian=date(2026, 6, 7),
            requester=admin,
            bot_identity=identity,
            sources=[source],
        )
        user_job = MonitoringJob(
            topic="User job",
            date_from_jalali="1405/03/01",
            date_to_jalali="1405/03/17",
            date_from_gregorian=date(2026, 5, 22),
            date_to_gregorian=date(2026, 6, 7),
            requester=user,
            bot_identity=identity,
            sources=[source],
        )
        report = AnalysisReport(
            job=admin_job,
            creator=admin,
            title="Admin report",
            summary="Admin-only report",
            full_report={"executive_summary": "Admin-only report"},
            sources_json=[],
        )
        session.add_all([admin, user, identity, source, admin_job, user_job, report])
        await session.flush()
        result = (str(admin.id), str(user.id), str(admin_job.id), str(user_job.id), str(report.id))
        await session.commit()
        return result


async def verify_security() -> None:
    marker = f"security-{uuid4().hex[:12]}"
    await cleanup(marker)
    try:
        admin_id, user_id, admin_job_id, user_job_id, report_id = await create_fixture(marker)
        async with httpx.AsyncClient(base_url="http://localhost:8000", timeout=30) as client:
            anonymous = await client.get("/api/v1/auth/me")
            assert anonymous.status_code == 401, anonymous.text

            bad_login = await client.post(
                "/api/v1/auth/login",
                json={"login": f"{marker}-user", "password": "WrongPass123"},
            )
            assert bad_login.status_code == 401, bad_login.text

            user_login = await client.post(
                "/api/v1/auth/login",
                json={"login": f"{marker}-user", "password": "UserPass123"},
            )
            assert user_login.status_code == 200, user_login.text
            user_tokens = user_login.json()
            user_headers = {"Authorization": f"Bearer {user_tokens['access_token']}"}

            me = await client.get("/api/v1/auth/me", headers=user_headers)
            assert me.status_code == 200, me.text
            assert me.json()["id"] == user_id

            denied_admin = await client.get("/api/v1/admin/users", headers=user_headers)
            assert denied_admin.status_code == 403, denied_admin.text

            hidden_job = await client.get(
                f"/api/v1/monitoring/jobs/{admin_job_id}", headers=user_headers
            )
            assert hidden_job.status_code == 404, hidden_job.text

            own_job = await client.get(
                f"/api/v1/monitoring/jobs/{user_job_id}", headers=user_headers
            )
            assert own_job.status_code == 200, own_job.text

            hidden_report = await client.get(f"/api/v1/reports/{report_id}", headers=user_headers)
            assert hidden_report.status_code == 404, hidden_report.text

            refresh = await client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": user_tokens["refresh_token"]},
            )
            assert refresh.status_code == 200, refresh.text

            reused_refresh = await client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": user_tokens["refresh_token"]},
            )
            assert reused_refresh.status_code == 401, reused_refresh.text

            admin_access, _ = create_access_token(admin_id, UserRole.SUPER_ADMIN)
            admin_headers = {"Authorization": f"Bearer {admin_access}"}
            admin_list = await client.get("/api/v1/admin/users", headers=admin_headers)
            assert admin_list.status_code == 200, admin_list.text
            assert admin_list.json()["meta"]["total"] >= 2

        print("Security verification passed: auth, refresh rotation, role checks, ownership checks")
    finally:
        await cleanup(marker)
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(verify_security())
