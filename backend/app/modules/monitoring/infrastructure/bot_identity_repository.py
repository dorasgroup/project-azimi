"""SQLAlchemy queries for analyst bot identities."""

from uuid import UUID
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import BotIdentity, MonitoringJob


class SqlAlchemyBotIdentityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list(self, page, page_size):
        total = int(await self._session.scalar(select(func.count()).select_from(BotIdentity)) or 0)
        rows = list(
            (
                await self._session.scalars(
                    select(BotIdentity)
                    .order_by(BotIdentity.is_default.desc(), BotIdentity.created_at.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).all()
        )
        return rows, total

    async def get(self, identity_id: UUID):
        return await self._session.get(BotIdentity, identity_id)

    async def has_default(self):
        return bool(
            await self._session.scalar(
                select(BotIdentity.id).where(BotIdentity.is_default.is_(True))
            )
        )

    async def active_default(self):
        return await self._session.scalar(
            select(BotIdentity).where(
                BotIdentity.is_default.is_(True), BotIdentity.is_active.is_(True)
            )
        )

    async def set_default(self, identity):
        await self._session.execute(update(BotIdentity).values(is_default=False))
        identity.is_default = True
        await self._session.flush()

    async def delete_with_jobs(self, identity):
        jobs = await self._session.scalars(
            select(MonitoringJob).where(MonitoringJob.bot_identity_id == identity.id)
        )
        for job in jobs.all():
            await self._session.delete(job)
        await self._session.delete(identity)

    async def add(self, identity):
        self._session.add(identity)
        await self._session.flush()

    async def flush(self):
        await self._session.flush()
