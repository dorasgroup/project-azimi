from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_non_guest, require_super_admin
from app.db.session import get_db_session
from app.models.category import Category
from app.repositories.category_repository import CategoryRepository
from app.schemas.auth import CurrentUserResponse
from app.schemas.category import (
    CategoryBaleAvailabilityUpdate,
    CategoryCreate,
    CategoryResponse,
)

router = APIRouter()


@router.post("", response_model=CategoryResponse)
async def create_category(
    payload: CategoryCreate,
    current_user: CurrentUserResponse = Depends(require_non_guest),
    session: AsyncSession = Depends(get_db_session),
) -> CategoryResponse:
    repo = CategoryRepository(session)
    existing = await repo.get_by_name(payload.name)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="دسته با این نام قبلا ثبت شده است.",
        )
    
    category = Category(name=payload.name)
    await repo.create(category)
    await session.commit()
    return CategoryResponse.model_validate(category)


@router.get("")
async def list_categories(
    current_user: CurrentUserResponse = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    repo = CategoryRepository(session)
    categories = await repo.list_all()
    items = [
        CategoryResponse.model_validate(cat).model_dump(mode="json")
        for cat in categories
    ]
    return {"items": items, "total": len(items)}


@router.put("/{category_id}", response_model=CategoryResponse)
async def update_category(
    category_id: UUID,
    payload: CategoryCreate,
    current_user: CurrentUserResponse = Depends(require_non_guest),
    session: AsyncSession = Depends(get_db_session),
) -> CategoryResponse:
    repo = CategoryRepository(session)
    category = await repo.get_by_id(category_id)
    if not category:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="دسته مورد نظر یافت نشد.",
        )
    
    # Check if name already exists in another category
    existing = await repo.get_by_name(payload.name)
    if existing and existing.id != category_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="دسته با این نام قبلا ثبت شده است.",
        )
        
    category.name = payload.name
    await session.commit()
    return CategoryResponse.model_validate(category)


@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(
    category_id: UUID,
    current_user: CurrentUserResponse = Depends(require_non_guest),
    session: AsyncSession = Depends(get_db_session),
):
    repo = CategoryRepository(session)
    category = await repo.get_by_id(category_id)
    if not category:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="دسته مورد نظر یافت نشد.",
        )
        
    await repo.delete(category)
    await session.commit()


@router.patch("/{category_id}/bale-availability", response_model=CategoryResponse)
async def update_bale_availability(
    category_id: UUID,
    payload: CategoryBaleAvailabilityUpdate,
    current_user: CurrentUserResponse = Depends(require_super_admin),
    session: AsyncSession = Depends(get_db_session),
) -> CategoryResponse:
    del current_user
    repo = CategoryRepository(session)
    category = await repo.get_by_id(category_id)
    if not category:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="دسته مورد نظر یافت نشد.",
        )
    category.available_in_bale = payload.available_in_bale
    await session.commit()
    return CategoryResponse.model_validate(category)
