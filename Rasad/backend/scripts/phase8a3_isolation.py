"""Fail-closed allowlist for this phase's disposable DB/broker, before writes."""

from urllib.parse import urlsplit


def assert_isolated(database_url: str, redis_url: str) -> None:
    db, broker = urlsplit(database_url), urlsplit(redis_url)
    if not (
        db.scheme == "postgresql+asyncpg"
        and db.hostname == "rasad-phase8-pg-letbr0"
        and db.port == 5432
        and db.username == "phase8"
        and db.path in ("/phase8a3_lifecycle", "/phase8a3_regression")
        and not db.query
        and broker.scheme == "redis"
        and broker.hostname == "rasad-phase8-redis-letbr0"
        and broker.port == 6379
        and broker.path == "/8"
        and not broker.query
    ):
        raise RuntimeError("Phase 8A-3 isolation allowlist refused; no writes permitted")
