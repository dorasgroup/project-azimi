from dataclasses import dataclass
from hmac import compare_digest
from uuid import UUID

from fastapi import Header, HTTPException, status

from app.core.config import settings


@dataclass(frozen=True)
class TrendIntegrationPrincipal:
    user_id: UUID


async def require_trend_integration(
    x_integration_token: str | None = Header(
        default=None,
        alias="X-Integration-Token",
    ),
) -> TrendIntegrationPrincipal:
    if not settings.trend_integration_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    configured = (
        settings.trend_integration_token.get_secret_value()
        if settings.trend_integration_token is not None
        else ""
    )
    if not configured or settings.trend_integration_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Trend integration is not configured",
        )
    if (
        x_integration_token is None
        or len(x_integration_token) > 256
        or not compare_digest(x_integration_token, configured)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid integration credential",
        )
    return TrendIntegrationPrincipal(user_id=settings.trend_integration_user_id)
