"""Minimal synthetic parents for the pre-existing full regression suite."""
import os
from scripts.test_environment import assert_safe_test_environment

# Reject endpoints BEFORE importing the global DB composition, connecting or writing.
assert_safe_test_environment(os.environ)

import asyncio
from uuid import uuid4

from sqlalchemy import select, func

from app.db.session import SessionFactory, engine
from app.models import User, BotIdentity, Source, SourceType


SYNTHETIC_USERNAME = "rasad-isolated-regression"
SYNTHETIC_EMAIL = "rasad-isolated-regression@example.invalid"


async def main():
    async with SessionFactory() as s, s.begin():
        user = await s.scalar(select(User).where(User.username == SYNTHETIC_USERNAME))
        if user is None:
            user = User(full_name="Synthetic regression",username=SYNTHETIC_USERNAME,
                        email=SYNTHETIC_EMAIL,password_hash="synthetic",is_active=False)
            s.add(user)
            await s.flush()
        elif (user.email, user.password_hash, user.is_active) != (SYNTHETIC_EMAIL, "synthetic", False):
            raise RuntimeError("Synthetic fixture owner collision; no writes permitted")
        if await s.scalar(select(BotIdentity).where(BotIdentity.created_by == user.id).limit(1)) is None:
            s.add(BotIdentity(title="Synthetic regression",role_definition="synthetic",created_by=user.id))
        count = await s.scalar(select(func.count()).select_from(Source).where(Source.created_by == user.id))
        for _ in range(max(0,2-count)):
            s.add(Source(title="Synthetic regression",base_url=f"https://{uuid4().hex}.example.invalid",
                         type=SourceType.NEWS_SITE,created_by=user.id))
    await engine.dispose()


if __name__=="__main__":
    asyncio.run(main())
