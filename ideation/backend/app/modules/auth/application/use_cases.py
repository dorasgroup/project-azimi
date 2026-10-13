import logging

from app.modules.auth.application.dto import LoginRequestDTO, TokenResponseDTO
from app.modules.auth.application.services import (
    create_access_token,
    create_refresh_token,
    verify_password,
)
from app.shared.application.unit_of_work import IUnitOfWork
from app.shared.domain.exceptions import AuthenticationException

logger = logging.getLogger(__name__)


class LoginUseCase:
    def __init__(self, uow: IUnitOfWork):
        self.uow = uow

    async def execute(self, req: LoginRequestDTO) -> (TokenResponseDTO, str):
        async with self.uow:
            user = await self.uow.users.get_by_email(req.email)
            if not user:
                logger.warning("prototype_login_failed outcome=user_not_found")
                raise AuthenticationException("Invalid email or password.")

            if not user.is_active:
                logger.warning("prototype_login_failed outcome=inactive")
                raise AuthenticationException("Account is disabled.")

            if not verify_password(req.password, user.password_hash):
                logger.warning("prototype_login_failed outcome=invalid_password")
                raise AuthenticationException("Invalid email or password.")

            access_token = create_access_token(data={"sub": str(user.id), "email": user.email})
            refresh_token = create_refresh_token(data={"sub": str(user.id)})

            # Return AccessToken DTO and RefreshToken (to be set in HttpOnly cookie)
            return TokenResponseDTO(access_token=access_token), refresh_token
