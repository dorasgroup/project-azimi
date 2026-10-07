import hmac
import os
from typing import Annotated

from fastapi import Depends, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.core.security import TokenError, decode_access_token
from app.modules.identity.domain.roles import UserRole

bearer_scheme = HTTPBearer(auto_error=False)


class Principal(BaseModel):
    subject: str
    role: UserRole


async def require_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> Principal:
    sso_secret = os.getenv("TEB_SSO_SECRET", "")
    sso_user_id = os.getenv("TEB_SSO_USER_ID", "")
    provided_sso_secret = request.headers.get("x-teb-sso", "")
    if (
        sso_secret
        and sso_user_id
        and provided_sso_secret
        and hmac.compare_digest(provided_sso_secret, sso_secret)
    ):
        return Principal(subject=sso_user_id, role=UserRole.SUPER_ADMIN)

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required"
        )
    try:
        payload = decode_access_token(credentials.credentials)
    except TokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return Principal(subject=payload["sub"], role=UserRole(payload["role"]))


async def require_admin(principal: Annotated[Principal, Depends(require_user)]) -> Principal:
    if principal.role != UserRole.SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Super admin access required"
        )
    return principal


class Pagination(BaseModel):
    page: int
    page_size: int


async def pagination(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Pagination:
    return Pagination(page=page, page_size=page_size)
