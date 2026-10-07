import os

from scripts.test_environment import assert_safe_test_environment

# Reject unsafe targets before importing application composition or fixtures.
assert_safe_test_environment(os.environ)

import asyncio
from uuid import UUID, uuid4

import httpx
from sqlalchemy import delete, select

from app.core.security import create_access_token, hash_password
from app.db.session import SessionFactory, engine
from app.models import LogLevel, SystemLog, User, UserRole
from app.modules.monitoring.infrastructure.log_operations import record_system_log


async def cleanup(marker: str) -> None:
    async with SessionFactory() as session:
        user = await session.scalar(select(User).where(User.username == marker))
        if user:
            await session.delete(user)
        await session.execute(delete(SystemLog).where(SystemLog.module == marker))
        await session.commit()


async def create_fixture(marker: str, request_id: UUID) -> str:
    async with SessionFactory() as session:
        admin = User(
            full_name="Observability Admin",
            username=marker,
            email=f"{marker}@example.com",
            password_hash=hash_password("ObservePass123"),
            role=UserRole.SUPER_ADMIN,
        )
        session.add(admin)
        await session.flush()
        await record_system_log(
            session,
            level=LogLevel.INFO,
            module=marker,
            message="Observability verification log",
            request_id=request_id,
            context={"verified": True},
        )
        await session.commit()
        return str(admin.id)


async def verify_observability() -> None:
    marker = f"observability-{uuid4().hex[:12]}"
    request_id = uuid4()
    await cleanup(marker)
    try:
        admin_id = await create_fixture(marker, request_id)
        access_token, _ = create_access_token(admin_id, UserRole.SUPER_ADMIN)
        headers = {"Authorization": f"Bearer {access_token}"}
        async with httpx.AsyncClient(base_url="http://localhost:8000", timeout=30) as client:
            health = await client.get("/api/v1/health", headers={"X-Request-ID": str(request_id)})
            assert health.status_code == 200, health.text
            assert health.headers["x-request-id"] == str(request_id)

            logs = await client.get(
                "/api/v1/system/logs",
                params={"module": marker, "level": "info"},
                headers=headers,
            )
            assert logs.status_code == 200, logs.text
            body = logs.json()
            assert body["meta"]["total"] >= 1
            assert body["items"][0]["request_id"] == str(request_id)
            assert body["items"][0]["context"]["verified"] is True

            anonymous = await client.get("/api/v1/system/logs", params={"module": marker})
            assert anonymous.status_code == 401, anonymous.text

        print(
            "Observability verification passed: request_id, structured logs endpoint, access control"
        )
    finally:
        await cleanup(marker)
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(verify_observability())
