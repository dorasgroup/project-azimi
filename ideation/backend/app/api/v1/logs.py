import logging

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.logging import log_event
from app.db.session import get_db_session
from app.schemas.auth import CurrentUserResponse
from app.schemas.system_log import FrontendLogRequest, SystemLogResponse
from app.services.system_log_service import SystemLogService

router = APIRouter()
logger = logging.getLogger("app.frontend")


@router.post("/frontend", response_model=SystemLogResponse, status_code=status.HTTP_201_CREATED)
async def create_frontend_log(
    payload: FrontendLogRequest,
    request: Request,
    current_user: CurrentUserResponse = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> SystemLogResponse:
    request_id = getattr(request.state, "request_id", None)
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    log_event(
        logger,
        logging.INFO,
        "frontend_log_received",
        request_id=request_id,
        method=request.method,
        route="/api/v1/logs/frontend",
        status_code=status.HTTP_201_CREATED,
        outcome="accepted",
    )

    return await SystemLogService(session).create_frontend_log(
        payload,
        actor_user_id=current_user.id,
        request_id=request_id,
        ip_address=client_ip,
        user_agent=user_agent,
    )
