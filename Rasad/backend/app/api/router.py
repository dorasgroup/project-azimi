from fastapi import APIRouter

from app.api.routes.admin_users import router as admin_users_router
from app.api.routes.auth import router as auth_router
from app.api.routes.bot_identities import router as bot_identities_router
from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.health import router as health_router
from app.api.routes.version import router as version_router
from app.api.routes.monitoring import router as monitoring_router
from app.api.routes.reports import router as reports_router
from app.api.routes.sources import router as sources_router
from app.api.routes.system_connections import router as system_connections_router
from app.api.routes.system_logs import router as system_logs_router
from app.schemas.common import ErrorResponse

api_router = APIRouter(
    responses={
        401: {"model": ErrorResponse, "description": "Authentication required"},
        403: {"model": ErrorResponse, "description": "Insufficient access"},
        404: {"model": ErrorResponse, "description": "Resource not found"},
        422: {"model": ErrorResponse, "description": "Request validation failed"},
    }
)
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(dashboard_router)
api_router.include_router(sources_router)
api_router.include_router(bot_identities_router)
api_router.include_router(monitoring_router)
api_router.include_router(reports_router)
api_router.include_router(admin_users_router)
api_router.include_router(system_logs_router)
api_router.include_router(system_connections_router)
api_router.include_router(version_router)
