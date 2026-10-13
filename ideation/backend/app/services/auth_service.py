from hmac import compare_digest
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import create_access_token, verify_password
from app.repositories.user_repository import UserRepository
from app.schemas.auth import CurrentUserResponse, LoginResponse


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.users = UserRepository(session)

    async def login(self, email: str, password: str) -> LoginResponse | None:
        user = await self.users.get_by_email(email)
        if not user or not user.is_active:
            return None
        if not verify_password(password, user.hashed_password):
            return None
        access_token = create_access_token(
            str(user.id),
            claims={"email": user.email, "role": user.role},
        )
        return LoginResponse(
            access_token=access_token,
            expires_in=settings.access_token_expire_minutes * 60,
            user=CurrentUserResponse(
                id=str(user.id),
                email=user.email,
                full_name=user.full_name,
                role=user.role,
                is_active=user.is_active,
            ),
        )

    async def get_current_user(self, user_id: UUID) -> CurrentUserResponse | None:
        user = await self.users.get_by_id(user_id)
        if not user or not user.is_active:
            return None
        return CurrentUserResponse(
            id=str(user.id),
            email=user.email,
            full_name=user.full_name,
            role=user.role,
            is_active=user.is_active,
        )

    async def guest_access(self, supplied_token: str) -> LoginResponse | None:
        expected_token = settings.guest_access_token_value
        guest_user_id = settings.guest_access_user_id
        if (
            not expected_token
            or guest_user_id is None
            or not compare_digest(supplied_token, expected_token)
        ):
            return None
        user = await self.users.get_by_id(guest_user_id)
        if not user or not user.is_active or user.role != "guest":
            return None
        access_token = create_access_token(
            str(user.id),
            claims={"email": user.email, "role": user.role, "access": "shared_link"},
        )
        return LoginResponse(
            access_token=access_token,
            expires_in=settings.access_token_expire_minutes * 60,
            user=CurrentUserResponse(
                id=str(user.id),
                email=user.email,
                full_name=user.full_name,
                role=user.role,
                is_active=user.is_active,
            ),
        )
