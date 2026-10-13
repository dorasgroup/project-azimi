from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_super_admin
from app.core.config import settings
from app.db.session import get_db_session
from app.schemas.auth import CurrentUserResponse
from app.schemas.system_log import SystemLogListResponse
from app.schemas.user import UserCreateRequest, UserResponse, UserUpdateRequest
from app.services.system_log_service import SystemLogService
from app.services.user_service import (
    CannotDeleteSelfError,
    EmailAlreadyExistsError,
    UserNotFoundError,
    UserService,
)

router = APIRouter()


@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreateRequest,
    _current_user: CurrentUserResponse = Depends(require_super_admin),
    session: AsyncSession = Depends(get_db_session),
) -> UserResponse:
    try:
        return await UserService(session).create_user(payload)
    except EmailAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already exists",
        ) from exc


@router.get("/users")
async def list_users(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    _current_user: CurrentUserResponse = Depends(require_super_admin),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    items, total = await UserService(session).list_users(page=page, limit=limit)
    return {
        "items": [item.model_dump(mode="json") for item in items],
        "page": page,
        "limit": limit,
        "total": total,
    }


@router.patch("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    payload: UserUpdateRequest,
    _current_user: CurrentUserResponse = Depends(require_super_admin),
    session: AsyncSession = Depends(get_db_session),
) -> UserResponse:
    try:
        return await UserService(session).update_user(user_id=user_id, payload=payload)
    except EmailAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already exists",
        ) from exc
    except UserNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        ) from exc


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: str,
    current_user: CurrentUserResponse = Depends(require_super_admin),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    try:
        await UserService(session).delete_user(user_id=user_id, current_user_id=current_user.id)
    except CannotDeleteSelfError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot delete your own account",
        ) from exc
    except UserNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        ) from exc


@router.get("/system-logs", response_model=SystemLogListResponse)
async def list_system_logs(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    level: str | None = Query(default=None, pattern="^(info|warning|error)$"),
    event_type: str | None = Query(default=None, max_length=100),
    _current_user: CurrentUserResponse = Depends(require_super_admin),
    session: AsyncSession = Depends(get_db_session),
) -> SystemLogListResponse:
    items, total = await SystemLogService(session).list_logs(
        page=page,
        limit=limit,
        level=level,
        event_type=event_type,
    )
    return SystemLogListResponse(items=items, page=page, limit=limit, total=total)


@router.delete("/system-logs")
async def clear_system_logs(
    _current_user: CurrentUserResponse = Depends(require_super_admin),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    deleted_count = await SystemLogService(session).clear_logs()
    return {"deleted": deleted_count}


@router.get("/log-files/{filename}")
async def read_log_file_tail(
    filename: str,
    lines: int = Query(default=120, ge=1, le=1000),
    _current_user: CurrentUserResponse = Depends(require_super_admin),
) -> dict:
    allowed_files = {"backend.log", "backend-errors.log"}
    if filename not in allowed_files:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Log file not found")

    path = settings.log_dir / filename
    if not path.exists():
        return {"filename": filename, "lines": []}

    content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return {"filename": filename, "lines": content[-lines:]}


@router.delete("/log-files/{filename}")
async def clear_log_file(
    filename: str,
    _current_user: CurrentUserResponse = Depends(require_super_admin),
) -> dict:
    allowed_files = {"backend.log", "backend-errors.log"}
    if filename not in allowed_files:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Log file not found")

    path = settings.log_dir / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    return {"filename": filename, "cleared": True}
