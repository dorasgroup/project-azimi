"""Server timer entrypoint: python -m app.integrations.haminyar.sync."""

import argparse
import asyncio
import json
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_sessionmaker
from app.integrations.haminyar.service import synchronize

LOCK_ID = 12687405640010


async def run(max_articles: int) -> dict:
    if not settings.site_article_sync_enabled:
        return {"status": "disabled"}
    engine = get_sessionmaker().kw["bind"]
    # Hold the advisory lock on one connection across all pipeline commits.
    async with engine.connect() as connection:
        locked = await connection.scalar(text("SELECT pg_try_advisory_lock(:id)"), {"id": LOCK_ID})
        await connection.commit()
        if not locked:
            return {"status": "already_running"}
        try:
            async with AsyncSession(bind=connection, expire_on_commit=False) as session:
                return await synchronize(session, max_articles=max_articles)
        finally:
            await connection.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": LOCK_ID})
            await connection.commit()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-articles", type=int, default=50)
    args = parser.parse_args()
    if not 1 <= args.max_articles <= 1000:
        parser.error("--max-articles must be between 1 and 1000")
    logging.basicConfig(level=logging.WARNING)
    try:
        result = asyncio.run(run(args.max_articles))
    except Exception:
        print(json.dumps({"status": "failed", "message": "See safe sync status in upload page"}))
        raise SystemExit(1) from None
    print(json.dumps(result))


if __name__ == "__main__":
    main()
