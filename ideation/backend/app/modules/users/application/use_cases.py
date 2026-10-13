import logging
from datetime import UTC, datetime
from uuid import uuid4

from app.modules.auth.application.services import get_password_hash
from app.modules.users.application.dto import CreateUserDTO, UserResponseDTO
from app.modules.users.domain.entities import User
from app.shared.application.unit_of_work import IUnitOfWork
from app.shared.domain.exceptions import ConflictException, ResourceNotFoundException

logger = logging.getLogger(__name__)


class CreateUserUseCase:
    def __init__(self, uow: IUnitOfWork):
        self.uow = uow

    async def execute(self, req: CreateUserDTO) -> UserResponseDTO:
        async with self.uow:
            existing_user = await self.uow.users.get_by_email(req.email)
            if existing_user:
                logger.warning("prototype_registration_failed outcome=email_exists")
                raise ConflictException("A user with this email already exists.")

            user = User(
                id=uuid4(),
                email=req.email,
                password_hash=get_password_hash(req.password),
                full_name=req.full_name,
                is_active=True,
                created_at=datetime.now(UTC),
            )

            await self.uow.users.save(user)
            await self.uow.commit()

            return UserResponseDTO.model_validate(user)


class GetUserProfileUseCase:
    def __init__(self, uow: IUnitOfWork):
        self.uow = uow

    async def execute(self, user_id: str) -> UserResponseDTO:
        async with self.uow:
            user = await self.uow.users.get_by_id(user_id)
            if not user:
                raise ResourceNotFoundException("User", user_id)
            return UserResponseDTO.model_validate(user)
