from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, user_id: UUID) -> User | None:
        return await self.session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        result = await self.session.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def get_by_email_excluding_id(self, email: str, user_id: UUID) -> User | None:
        result = await self.session.execute(
            select(User).where(User.email == email, User.id != user_id)
        )
        return result.scalar_one_or_none()

    async def create(self, user: User) -> User:
        self.session.add(user)
        await self.session.flush()
        return user

    async def soft_delete(self, user: User) -> User:
        user.deleted_at = datetime.now(UTC)
        user.is_active = False
        await self.session.flush()
        return user

    async def update(self, user: User) -> User:
        await self.session.flush()
        return user

    async def list(self, limit: int = 50, offset: int = 0) -> tuple[list[User], int]:
        items_result = await self.session.execute(
            select(User)
            .where(User.deleted_at.is_(None))
            .order_by(User.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        total_result = await self.session.execute(
            select(func.count()).select_from(User).where(User.deleted_at.is_(None))
        )
        return list(items_result.scalars().all()), int(total_result.scalar_one())
