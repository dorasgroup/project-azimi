from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.api.integration_deps import (
    TrendIntegrationPrincipal,
    require_trend_integration,
)
from app.db.session import get_db_session
from app.schemas.auth import CurrentUserResponse
from app.schemas.trend_integration import (
    TrendImportListResponse,
    TrendImportRequest,
    TrendImportResponse,
)
from app.services.trend_import_service import (
    TrendImportConflict,
    TrendIntegrationConfigurationError,
    list_trend_imports,
    receive_trend,
    trend_status,
)

router = APIRouter()


@router.get("", response_model=TrendImportListResponse)
async def list_imported_trends(
    current_user: Annotated[CurrentUserResponse, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> TrendImportListResponse:
    del current_user
    return await list_trend_imports(session)


@router.post("", response_model=TrendImportResponse, status_code=status.HTTP_202_ACCEPTED)
async def import_trend(
    payload: TrendImportRequest,
    request: Request,
    principal: Annotated[TrendIntegrationPrincipal, Depends(require_trend_integration)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=16, max_length=255),
    ],
) -> TrendImportResponse:
    try:
        return await receive_trend(
            session,
            payload=payload,
            idempotency_key=idempotency_key,
            user_id=principal.user_id,
            request_id=request.state.request_id,
        )
    except TrendImportConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The trend identifier was already used with different content",
        ) from exc
    except TrendIntegrationConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Trend integration user is not configured",
        ) from exc


@router.get("/{source_trend_id}", response_model=TrendImportResponse)
async def get_trend_status(
    source_trend_id: str,
    principal: Annotated[TrendIntegrationPrincipal, Depends(require_trend_integration)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    source_system: str = "trend-monitor",
    source_version: int = 1,
) -> TrendImportResponse:
    del principal
    result = await trend_status(
        session,
        source_system=source_system,
        source_trend_id=source_trend_id,
        source_version=source_version,
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Trend import not found",
        )
    return result
