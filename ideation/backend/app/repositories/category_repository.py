from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category


class CategoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_name(self, name: str) -> Category | None:
        result = await self.session.execute(
            select(Category).where(Category.name == name)
        )
        return result.scalar_one_or_none()

    async def create(self, category: Category) -> Category:
        self.session.add(category)
        await self.session.flush()
        return category

    async def list_all(self) -> list[Category]:
        result = await self.session.execute(
            select(Category).order_by(Category.name.asc())
        )
        return list(result.scalars().all())

    async def list_available_in_bale(self) -> list[Category]:
        result = await self.session.execute(
            select(Category)
            .where(Category.available_in_bale.is_(True))
            .order_by(Category.name.asc())
        )
        return list(result.scalars().all())

    async def get_by_id(self, category_id) -> Category | None:
        result = await self.session.execute(
            select(Category).where(Category.id == category_id)
        )
        return result.scalar_one_or_none()

    async def delete(self, category: Category) -> None:
        await self.session.delete(category)
