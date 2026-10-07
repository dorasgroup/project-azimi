"""Deterministic and lightweight healthcheck probe for Celery worker.

Verifies:
1. Celery control path is responsive.
2. The worker process responds to ping.
3. Broker / Redis connectivity is alive.
"""

import os
import sys

from celery import Celery


def check_worker_health(timeout: float = 3.0) -> int:
    try:
        broker = os.getenv("CELERY_BROKER_URL", "redis://rasad-redis:6379/0")
        app = Celery("healthcheck", broker=broker)
        hostname = os.getenv("HOSTNAME")
        worker_name = os.getenv("CELERY_WORKER_NAME", "celery")
        destination = [f"{worker_name}@{hostname}"] if hostname else None
        insp = app.control.inspect(destination=destination, timeout=timeout)
        pings = insp.ping()
        if not pings and destination:
            # Fallback to general ping if destination name format differed
            insp = app.control.inspect(timeout=timeout)
            pings = insp.ping()
        if not pings:
            print("Worker healthcheck failed: no response received from worker", file=sys.stderr)
            return 1
        return 0
    except Exception as exc:
        print(f"Worker healthcheck exception: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(check_worker_health())
