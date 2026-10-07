"""SQLAlchemy repository for user and refresh-token queries."""

from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import RefreshToken, User
from app.shared.domain.errors import ConflictError


class SqlAlchemyUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_login(self, login: str):
        return await self._session.scalar(
            select(User).where(or_(User.username == login, User.email == login))
        )

    async def get(self, user_id: UUID):
        return await self._session.get(User, user_id)

    async def refresh(self, token_hash: str):
        query = (
            select(RefreshToken)
            .where(RefreshToken.token_hash == token_hash)
            .options(selectinload(RefreshToken.user))
        )
        return await self._session.scalar(query)

    async def list(self, page: int, page_size: int):
        total = int(await self._session.scalar(select(func.count()).select_from(User)) or 0)
        query = (
            select(User)
            .order_by(User.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = list((await self._session.scalars(query)).all())
        return rows, total

    async def flush(self) -> None:
        await self._flush_with_conflict_translation()

    async def delete(self, user: User) -> None:
        await self._session.delete(user)

    async def add(self, entity: User | RefreshToken) -> None:
        self._session.add(entity)
        await self._flush_with_conflict_translation()

    async def refresh_by_hash(self, token_hash: str):
        return await self._session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )

    async def _flush_with_conflict_translation(self) -> None:
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise ConflictError("Username or email already exists") from exc
