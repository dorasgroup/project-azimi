"""Dependency-free, fail-closed test target validation BEFORE any external IO.

Only the dedicated Phase 8 disposable services are approved here. Production
defaults and .env files are never read, and no override bypass is provided.
Network membership must additionally be checked by the container harness.
"""

from collections.abc import Mapping
import os
from urllib.parse import urlsplit


DATABASES = frozenset({
    "phase8_release", "phase8_recovery", "phase8a3_lifecycle",
    "phase8a3_regression", "phase89_release", "phase9_e2e",
})
POSTGRES_HOST = "rasad-phase8-pg-letbr0"
REDIS_HOST = "rasad-phase8-redis-letbr0"


def assert_safe_test_database(url: str) -> None:
    try:
        target = urlsplit(url)
        accepted = (
            target.scheme == "postgresql+asyncpg"
            and target.hostname == POSTGRES_HOST
            and target.port == 5432
            and target.username == "phase8"
            and target.path.lstrip("/") in DATABASES
            and target.path == "/" + target.path.lstrip("/")
            and not target.query and not target.fragment
        )
    except ValueError:
        accepted = False
    if not accepted:
        raise RuntimeError("Unsafe test database target refused before IO")


def assert_safe_test_redis(url: str, *, result_backend: bool = False) -> int:
    try:
        target = urlsplit(url)
        accepted = (
            target.scheme == "redis"
            and target.hostname == REDIS_HOST
            and target.port == 6379
            and target.path in ({"/1", "/9", "/11"} if result_backend else {"/0", "/8", "/10"})
            and not target.query and not target.fragment
        )
    except ValueError:
        accepted = False
    if not accepted:
        raise RuntimeError("Unsafe test Redis target refused before IO")
    return int(target.path[1:])


def assert_safe_test_environment(environment: Mapping[str, str]) -> None:
    required = ("DATABASE_URL", "REDIS_URL", "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND")
    if any(not environment.get(key) for key in required):
        raise RuntimeError("Explicit isolated DB and Redis test settings required; no .env fallback")
    assert_safe_test_database(environment["DATABASE_URL"])
    main = assert_safe_test_redis(environment["REDIS_URL"])
    broker = assert_safe_test_redis(environment["CELERY_BROKER_URL"])
    results = assert_safe_test_redis(environment["CELERY_RESULT_BACKEND"], result_backend=True)
    if main != broker or results != broker + 1:
        raise RuntimeError("Inconsistent isolated test Redis DB mapping refused before IO")
    if (broker == 10) != environment["DATABASE_URL"].endswith("/phase9_e2e"):
        raise RuntimeError("E2E database and broker require their dedicated mapping")
    for key in ("PHASE8A3_DB_URL",):
        if environment.get(key):
            assert_safe_test_database(environment[key])
            if environment[key] != environment["DATABASE_URL"]:
                raise RuntimeError("Alternate test DB must match the guarded runtime DB")
    if environment.get("PHASE8A3_REDIS_URL"):
        assert_safe_test_redis(environment["PHASE8A3_REDIS_URL"])
        if environment["PHASE8A3_REDIS_URL"] != environment["CELERY_BROKER_URL"]:
            raise RuntimeError("Alternate test Redis must match the guarded broker")


if __name__ == "__main__":
    assert_safe_test_environment(os.environ)
    print("Isolated test targets validated; no database/broker connection opened")
