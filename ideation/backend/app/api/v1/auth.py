from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db_session
from app.schemas.auth import CurrentUserResponse, GuestAccessRequest, LoginRequest, LoginResponse
from app.schemas.user import ProfileUpdateRequest
from app.services.auth_service import AuthService
from app.services.user_service import EmailAlreadyExistsError, UserNotFoundError, UserService

router = APIRouter()


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    session: AsyncSession = Depends(get_db_session),
) -> LoginResponse:
    result = await AuthService(session).login(payload.email, payload.password)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    return result


@router.post("/guest-access", response_model=LoginResponse)
async def guest_access(
    payload: GuestAccessRequest,
    session: AsyncSession = Depends(get_db_session),
) -> LoginResponse:
    result = await AuthService(session).guest_access(payload.token)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked shared link",
        )
    return result


@router.get("/me", response_model=CurrentUserResponse)
async def me(current_user: CurrentUserResponse = Depends(get_current_user)) -> CurrentUserResponse:
    return current_user


@router.patch("/me", response_model=CurrentUserResponse)
async def update_me(
    payload: ProfileUpdateRequest,
    current_user: CurrentUserResponse = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> CurrentUserResponse:
    if current_user.role == "guest":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Guest profile cannot be modified",
        )
    try:
        updated_user = await UserService(session).update_profile(current_user.id, payload)
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
    return CurrentUserResponse(
        id=updated_user.id,
        email=updated_user.email,
        full_name=updated_user.full_name,
        role=updated_user.role,
        is_active=updated_user.is_active,
    )
