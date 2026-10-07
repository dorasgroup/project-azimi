import os

from scripts.test_environment import assert_safe_test_environment

# Reject unsafe targets before importing application composition or fixtures.
assert_safe_test_environment(os.environ)

import asyncio
from uuid import uuid4

import httpx
from sqlalchemy import delete, select, update

from app.core.security import hash_password
from app.db.session import SessionFactory, engine
from app.models import BotIdentity, MonitoringJob, Source, User, UserRole


API_BASE_URL = "http://localhost:8000/api/v1"
MVP_PASSWORD = "MvpDemoPass123!"


async def cleanup(username: str) -> None:
    async with SessionFactory() as session:
        user = await session.scalar(select(User).where(User.username == username))
        if user:
            await session.execute(
                delete(MonitoringJob).where(MonitoringJob.requested_by == user.id)
            )
            await session.execute(delete(Source).where(Source.created_by == user.id))
            await session.execute(delete(BotIdentity).where(BotIdentity.created_by == user.id))
            await session.delete(user)
        await session.commit()


async def ensure_default_identity() -> None:
    async with SessionFactory() as session:
        default_exists = bool(
            await session.scalar(select(BotIdentity.id).where(BotIdentity.is_default.is_(True)))
        )
        if not default_exists:
            identity = await session.scalar(
                select(BotIdentity).where(BotIdentity.is_active.is_(True))
            )
            if identity:
                await session.execute(update(BotIdentity).values(is_default=False))
                identity.is_default = True
        await session.commit()


async def create_mvp_user(username: str) -> None:
    async with SessionFactory() as session:
        session.add(
            User(
                full_name="MVP Demo Admin",
                username=username,
                email=f"{username}@example.com",
                password_hash=hash_password(MVP_PASSWORD),
                role=UserRole.SUPER_ADMIN,
                is_active=True,
            )
        )
        await session.commit()


async def auth_headers(client: httpx.AsyncClient, username: str) -> dict[str, str]:
    response = await client.post(
        "/auth/login",
        json={"login": username, "password": MVP_PASSWORD},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def create_source(client: httpx.AsyncClient, headers: dict[str, str]) -> str:
    response = await client.post(
        "/sources",
        headers=headers,
        json={
            "title": "Python Blog MVP Source",
            "base_url": "https://blog.python.org",
            "type": "news_site",
            "is_active": True,
            "crawl_config": {
                "crawl_policy": {
                    "max_pages": 3,
                    "max_concurrency": 2,
                    "render_javascript": False,
                },
                "processing_policy": {
                    "min_text_length": 100,
                    "min_matched_score": 0.01,
                },
            },
        },
    )
    if response.status_code == 409:
        sources = await client.get("/sources?page_size=100", headers=headers)
        assert sources.status_code == 200, sources.text
        source_id = next(
            item["id"]
            for item in sources.json()["items"]
            if item["base_url"].rstrip("/") == "https://blog.python.org"
        )
    else:
        assert response.status_code == 201, response.text
        source_id = response.json()["id"]

    check = await client.post(f"/sources/{source_id}/check", headers=headers)
    assert check.status_code == 200, check.text
    assert check.json()["status"] == "success", check.text
    return source_id


async def create_identity(client: httpx.AsyncClient, headers: dict[str, str]) -> str:
    response = await client.post(
        "/bot-identities",
        headers=headers,
        json={
            "title": "MVP Cognitive Analyst",
            "description": "Demo identity for the local MVP run",
            "role_definition": "Analyze selected news sources for cognitive and narrative patterns.",
            "tone": "formal",
            "analysis_style": "structured",
            "cognitive_framework": "narratives, actors, framing, persuasion signals",
            "output_structure": {"format": "json", "sections": ["summary", "findings", "sources"]},
            "source_policy": {"require_citations": True},
            "is_active": True,
        },
    )
    assert response.status_code == 201, response.text
    identity_id = response.json()["id"]

    active = await client.put(
        "/bot-identities/active",
        headers=headers,
        json={"identity_id": identity_id},
    )
    assert active.status_code == 200, active.text
    assert active.json()["id"] == identity_id
    return identity_id


async def run_monitoring_job(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    source_id: str,
    identity_id: str,
) -> tuple[str, str]:
    response = await client.post(
        "/monitoring/jobs",
        headers=headers,
        json={
            "topic": "Python",
            "date_from_jalali": "1405/03/01",
            "date_to_jalali": "1405/03/17",
            "source_ids": [source_id],
            "bot_identity_id": identity_id,
        },
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]

    for _ in range(60):
        job = await client.get(f"/monitoring/jobs/{job_id}", headers=headers)
        assert job.status_code == 200, job.text
        body = job.json()
        if body["status"] in {"completed", "failed", "cancelled"}:
            break
        await asyncio.sleep(2)
    else:
        raise AssertionError("MVP monitoring job did not reach a terminal state")

    assert body["status"] == "completed", body

    reports = await client.get("/reports", headers=headers)
    assert reports.status_code == 200, reports.text
    related = [item for item in reports.json()["items"] if item["job_id"] == job_id]
    assert related, reports.text
    report_id = related[0]["id"]
    return job_id, report_id


async def verify_report_downloads(
    client: httpx.AsyncClient, headers: dict[str, str], report_id: str
) -> None:
    report = await client.get(f"/reports/{report_id}", headers=headers)
    assert report.status_code == 200, report.text
    body = report.json()
    assert body["pdf_available"] is True
    assert body["docx_available"] is True
    assert body["sources"], "MVP report should include traceable sources"

    pdf = await client.get(f"/reports/{report_id}/download/pdf", headers=headers)
    assert pdf.status_code == 200, pdf.text
    assert pdf.headers["content-type"].startswith("application/pdf")
    assert len(pdf.content) > 100

    docx = await client.get(f"/reports/{report_id}/download/docx", headers=headers)
    assert docx.status_code == 200, docx.text
    assert "wordprocessingml.document" in docx.headers["content-type"]
    assert len(docx.content) > 100


async def verify_mvp() -> None:
    username = f"mvp-admin-{uuid4().hex[:10]}"
    await cleanup(username)
    try:
        await create_mvp_user(username)
        async with httpx.AsyncClient(base_url=API_BASE_URL, timeout=30) as client:
            health = await client.get("/health")
            assert health.status_code == 200, health.text
            headers = await auth_headers(client, username)

            source_id = await create_source(client, headers)
            identity_id = await create_identity(client, headers)
            job_id, report_id = await run_monitoring_job(client, headers, source_id, identity_id)
            await verify_report_downloads(client, headers, report_id)

        print(
            "MVP verification passed: "
            f"user={username}, source={source_id}, identity={identity_id}, "
            f"job={job_id}, report={report_id}"
        )
    finally:
        await cleanup(username)
        await ensure_default_identity()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(verify_mvp())
