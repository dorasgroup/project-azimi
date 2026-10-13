from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.schemas.user import ProfileUpdateRequest, UserCreateRequest, UserResponse, UserUpdateRequest


class EmailAlreadyExistsError(ValueError):
    pass


class UserNotFoundError(ValueError):
    pass


class CannotDeleteSelfError(ValueError):
    pass


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)

    async def create_user(self, payload: UserCreateRequest) -> UserResponse:
        existing = await self.users.get_by_email(payload.email)
        if existing:
            raise EmailAlreadyExistsError("Email already exists")
        user = User(
            email=payload.email,
            full_name=payload.full_name,
            hashed_password=hash_password(payload.password),
            role=payload.role,
            is_active=True,
        )
        await self.users.create(user)
        await self.session.commit()
        return self._to_response(user)

    async def list_users(self, page: int, limit: int) -> tuple[list[UserResponse], int]:
        offset = (page - 1) * limit
        users, total = await self.users.list(limit=limit, offset=offset)
        return [self._to_response(user) for user in users], total

    async def update_user(self, user_id: str, payload: UserUpdateRequest) -> UserResponse:
        user = await self._get_existing_user(user_id)
        update_data = payload.model_dump(exclude_unset=True)
        await self._apply_update(user, update_data)
        await self.users.update(user)
        await self.session.commit()
        return self._to_response(user)

    async def update_profile(self, user_id: str, payload: ProfileUpdateRequest) -> UserResponse:
        user = await self._get_existing_user(user_id)
        update_data = payload.model_dump(exclude_unset=True)
        await self._apply_update(user, update_data)
        await self.users.update(user)
        await self.session.commit()
        return self._to_response(user)

    async def delete_user(self, user_id: str, current_user_id: str) -> None:
        if user_id == current_user_id:
            raise CannotDeleteSelfError("Cannot delete yourself")

        user = await self._get_existing_user(user_id)

        await self.users.soft_delete(user)
        await self.session.commit()

    async def _get_existing_user(self, user_id: str) -> User:
        try:
            parsed_user_id = UUID(user_id)
        except ValueError as exc:
            raise UserNotFoundError("User not found") from exc

        user = await self.users.get_by_id(parsed_user_id)
        if user is None or user.deleted_at is not None:
            raise UserNotFoundError("User not found")
        return user

    async def _apply_update(self, user: User, update_data: dict) -> None:
        email = update_data.get("email")
        if email is not None and email != user.email:
            existing = await self.users.get_by_email_excluding_id(email, user.id)
            if existing:
                raise EmailAlreadyExistsError("Email already exists")
            user.email = email

        if update_data.get("full_name") is not None:
            user.full_name = update_data["full_name"]
        if update_data.get("password") is not None:
            user.hashed_password = hash_password(update_data["password"])
        if update_data.get("role") is not None:
            user.role = update_data["role"]
        if update_data.get("is_active") is not None:
            user.is_active = update_data["is_active"]

    def _to_response(self, user: User) -> UserResponse:
        return UserResponse(
            id=str(user.id),
            email=user.email,
            full_name=user.full_name,
            role=user.role,
            is_active=user.is_active,
            created_at=user.created_at,
            last_login_at=user.last_login_at,
        )
