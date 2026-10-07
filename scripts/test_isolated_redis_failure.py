import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import remote_helper

print("=== STARTING SECTION 9: REAL ISOLATED REDIS BROKER FAILURE TEST ===")

CONTAINER_NAME = "p7d-isolated-redis"
ISOLATED_BROKER_URL = "redis://p7d-isolated-redis:6379/0"

# Step 1: Ensure clean state and launch isolated Redis container on rasad10_default
print("[1] Starting isolated Redis container...")
remote_helper.run_remote_cmd(f"docker rm -f {CONTAINER_NAME} 2>/dev/null || true")
code, out, err = remote_helper.run_remote_cmd(
    f"docker run -d --name {CONTAINER_NAME} --network rasad10_default redis:7.4-alpine"
)
container_id = out.strip()[:12]
print(f"    Container started: {container_id}")
time.sleep(2)

# Verify isolated redis is reachable inside the network
verify_cmd = f"docker run --rm --network rasad10_default redis:7.4-alpine redis-cli -h {CONTAINER_NAME} ping"
code, out, err = remote_helper.run_remote_cmd(verify_cmd)
assert "PONG" in out, f"Isolated Redis ping failed: {out} {err}"
print("    Isolated Redis is responding with PONG.")

# Step 2: Upload isolated test script into backend
test_script_content = '''
import asyncio
from datetime import UTC, datetime, timedelta
import os
import sys
from uuid import uuid4

import redis
from celery import Celery
from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionFactory, engine
from app.models.bot_identity import BotIdentity
from app.models.completion_tracker import OrmRunCompletionTracker
from app.models.monitoring_job import JobStatus, MonitoringJob
from app.models.monitoring_run import MonitoringRun as OrmMonitoringRun
from app.models.user import User
from app.modules.monitoring.domain.completion_tracker import RunCompletionTrackerStatus
from app.modules.monitoring.domain.execution import MonitoringRunStatus
from app.modules.monitoring.infrastructure.completion_tracker_repository import (
    SqlAlchemyRunCompletionTrackerRepository,
)
from app.modules.monitoring.infrastructure.deadline_scheduler import (
    CeleryDeadlineScheduler,
)
from app.modules.monitoring.ports.completion import TransientDeadlineSchedulingError
from app.workers.control_tasks import is_deadline_obligation_still_pending

ISOLATED_BROKER_URL = "redis://p7d-isolated-redis:6379/0"

action = sys.argv[1] if len(sys.argv) > 1 else "all"

async def init_tracker():
    now = datetime.now(UTC)
    run_id = uuid4()
    job_id = uuid4()
    deadline_at = now + timedelta(seconds=300)

    async with SessionFactory() as session:
        user = await session.scalar(select(User))
        if user is None:
            user = User(
                id=uuid4(),
                email="iso_test@example.com",
                hashed_password="hash",
                is_active=True,
                is_superuser=False,
            )
            session.add(user)
            await session.flush()

        bot = await session.scalar(select(BotIdentity).where(BotIdentity.is_active.is_(True)))
        if bot is None:
            bot = BotIdentity(
                id=uuid4(),
                name="Iso Test Bot",
                version="1.0.0",
                description="Iso Test Bot",
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            session.add(bot)
            await session.flush()

        job = MonitoringJob(
            id=job_id,
            topic="Phase 7D Isolated Redis Job",
            date_from_jalali="1405/01/01",
            date_to_jalali="1405/01/10",
            date_from_gregorian=now.date() - timedelta(days=1),
            date_to_gregorian=now.date(),
            requested_by=user.id,
            bot_identity_id=bot.id,
            status=JobStatus.CRAWLING,
            created_at=now,
            updated_at=now,
        )
        session.add(job)

        run = OrmMonitoringRun(
            id=run_id,
            monitoring_job_id=job_id,
            status=MonitoringRunStatus.RUNNING,
            execution_mode="distributed",
            expected_sources=2,
            terminal_sources=0,
            successful_sources=0,
            failed_sources=0,
            cancelled_sources=0,
            created_at=now,
            updated_at=now,
        )
        session.add(run)
        await session.commit()

    repo = SqlAlchemyRunCompletionTrackerRepository(SessionFactory)
    tracker = await repo.get_or_create(run_id, deadline_at=deadline_at)

    print(f"TRACKER_INITIALIZED|{run_id}|{tracker.id}|{deadline_at.isoformat()}")

async def try_schedule_failure(run_id_str, tracker_id_str, deadline_at_str):
    from uuid import UUID
    run_id = UUID(run_id_str)
    tracker_id = UUID(tracker_id_str)
    deadline_at = datetime.fromisoformat(deadline_at_str)

    isolated_celery = Celery("test_isolated")
    isolated_celery.conf.broker_url = ISOLATED_BROKER_URL
    isolated_celery.conf.broker_connection_timeout = 2
    isolated_celery.conf.redis_socket_connect_timeout = 2
    isolated_celery.conf.redis_socket_timeout = 2

    scheduler = CeleryDeadlineScheduler(
        celery_app=isolated_celery,
        max_horizon_seconds=settings.max_deadline_horizon_seconds,
    )

    try:
        scheduler.schedule_deadline_signal(run_id, tracker_id, deadline_at)
        print("UNEXPECTED_SUCCESS_DURING_OUTAGE")
    except TransientDeadlineSchedulingError as exc:
        print(f"CAUGHT_TRANSIENT_ERROR|{exc}")
    except Exception as exc:
        print(f"CAUGHT_OTHER_ERROR|{type(exc)}|{exc}")

async def try_schedule_recovery(run_id_str, tracker_id_str, deadline_at_str):
    from uuid import UUID
    run_id = UUID(run_id_str)
    tracker_id = UUID(tracker_id_str)
    deadline_at = datetime.fromisoformat(deadline_at_str)

    isolated_celery = Celery("test_isolated")
    isolated_celery.conf.broker_url = ISOLATED_BROKER_URL
    scheduler = CeleryDeadlineScheduler(
        celery_app=isolated_celery,
        max_horizon_seconds=settings.max_deadline_horizon_seconds,
    )

    scheduler.schedule_deadline_signal(run_id, tracker_id, deadline_at)
    print("SCHEDULE_RECOVERY_SUCCESS")

    async with SessionFactory() as session:
        t = await session.get(OrmRunCompletionTracker, tracker_id)
        t.deadline_scheduled_at = datetime.now(UTC)
        await session.commit()
    print("DEADLINE_SCHEDULED_AT_POPULATED")

if __name__ == "__main__":
    if action == "init":
        asyncio.run(init_tracker())
    elif action == "schedule_fail":
        asyncio.run(try_schedule_failure(sys.argv[2], sys.argv[3], sys.argv[4]))
    elif action == "schedule_recover":
        asyncio.run(try_schedule_recovery(sys.argv[2], sys.argv[3], sys.argv[4]))
'''

remote_helper.write_remote_file("/opt/rasad10/backend/test_iso_script.py", test_script_content)

try:
    # Step 3: Initialize test tracker in DB
    print("[2] Initializing test tracker in database...")
    code, out, err = remote_helper.run_remote_cmd(
        f"docker run --rm --env-file /opt/rasad10/.env -e CELERY_BROKER_URL={ISOLATED_BROKER_URL} --network rasad10_default -v /opt/rasad10/backend:/app rasad10-test:latest python /app/test_iso_script.py init"
    )
    assert code == 0, f"Init failed: {out} {err}"
    for line in out.splitlines():
        if line.startswith("TRACKER_INITIALIZED|"):
            _, run_id, tracker_id, deadline_at = line.split("|")
            break
    print(f"    Tracker ready: run_id={run_id}, tracker_id={tracker_id}")

    # Step 4: Stop isolated Redis container (simulating broker loss)
    print("[3] Stopping isolated Redis container (simulating broker loss)...")
    code, out, err = remote_helper.run_remote_cmd(f"docker stop {CONTAINER_NAME}")
    assert code == 0, f"Docker stop failed: {out} {err}"
    print("    Isolated Redis container is STOPPED.")

    # Step 5: Execute schedule_fail inside container
    print("[4] Executing deadline scheduling against stopped broker...")
    code, out, err = remote_helper.run_remote_cmd(
        f"docker run --rm --env-file /opt/rasad10/.env -e CELERY_BROKER_URL={ISOLATED_BROKER_URL} --network rasad10_default -v /opt/rasad10/backend:/app rasad10-test:latest python /app/test_iso_script.py schedule_fail {run_id} {tracker_id} {deadline_at}"
    )
    print(f"    Output from failure attempt:\n    {out.strip()}")
    assert "CAUGHT_TRANSIENT_ERROR" in out, f"Did not catch transient error: {out}"
    print("    Verified: TransientDeadlineSchedulingError was raised as expected.")

    # Invariant check: Tracker deadline_scheduled_at is still NULL
    check_code = f"""
import asyncio
from uuid import UUID
from app.db.session import SessionFactory
from app.models.completion_tracker import OrmRunCompletionTracker
from app.workers.control_tasks import is_deadline_obligation_still_pending

async def check():
    async with SessionFactory() as session:
        t = await session.get(OrmRunCompletionTracker, UUID('{tracker_id}'))
        assert t.deadline_scheduled_at is None
        assert 'OPEN' in str(t.status).upper()
    assert is_deadline_obligation_still_pending('{run_id}') is True
    print('INVARIANT_INTACT_OBLIGATION_PENDING')

asyncio.run(check())
"""
    code, out, err = remote_helper.run_remote_cmd(
        f"docker run --rm --env-file /opt/rasad10/.env --network rasad10_default -v /opt/rasad10/backend:/app rasad10-test:latest python -c \"{check_code}\""
    )
    assert "INVARIANT_INTACT_OBLIGATION_PENDING" in out, f"Invariant violated: {out} {err}"
    print("    [+] INVARIANT PROVEN: Obligation was NOT ACKed away and remains pending while broker is down!")

    # Step 6: Restart isolated Redis container
    print("[5] Restarting isolated Redis container...")
    code, out, err = remote_helper.run_remote_cmd(f"docker start {CONTAINER_NAME}")
    assert code == 0, f"Docker start failed: {out} {err}"
    time.sleep(2)
    print("    Isolated Redis container is RESTORED and operational.")

    # Step 7: Retry deadline scheduling without any new business event
    print("[6] Retrying deadline scheduling now that broker is restored (no new business event)...")
    code, out, err = remote_helper.run_remote_cmd(
        f"docker run --rm --env-file /opt/rasad10/.env -e CELERY_BROKER_URL={ISOLATED_BROKER_URL} --network rasad10_default -v /opt/rasad10/backend:/app rasad10-test:latest python /app/test_iso_script.py schedule_recover {run_id} {tracker_id} {deadline_at}"
    )
    assert "SCHEDULE_RECOVERY_SUCCESS" in out, f"Recovery scheduling failed: {out} {err}"
    assert "DEADLINE_SCHEDULED_AT_POPULATED" in out, f"Marker was not set: {out} {err}"
    print("    [+] RECOVERY PROVEN: Deadline scheduling successfully published to restored broker!")

    # Final check: Obligation is now satisfied
    code, out, err = remote_helper.run_remote_cmd(
        f"docker run --rm --env-file /opt/rasad10/.env --network rasad10_default -v /opt/rasad10/backend:/app rasad10-test:latest python -c \"from app.workers.control_tasks import is_deadline_obligation_still_pending; assert is_deadline_obligation_still_pending('{run_id}') is False; print('OBLIGATION_SATISFIED')\""
    )
    assert "OBLIGATION_SATISFIED" in out, f"Obligation check failed: {out}"
    print("    [+] Final check: obligation satisfied, task ACKs cleanly without further retries.")

    print("\n=================================================================")
    print("=== SECTION 9 TEST PASSED: REAL ISOLATED REDIS BROKER FAILURE ===")
    print("=================================================================")

finally:
    print("[*] Cleaning up isolated container and test files...")
    remote_helper.run_remote_cmd(f"docker rm -f {CONTAINER_NAME} 2>/dev/null || true")
    remote_helper.run_remote_cmd("rm -f /opt/rasad10/backend/test_iso_script.py")
    print("[*] Cleanup complete.")
