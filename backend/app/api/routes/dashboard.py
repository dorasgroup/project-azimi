from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.dependencies import Principal, require_user
from app.api.operations.observability import QueryOperations
from app.bootstrap.providers import provide_query_operations
from app.schemas.dashboard import DashboardSummary

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
Queries = Annotated[QueryOperations, Depends(provide_query_operations)]


@router.get("/summary", response_model=DashboardSummary)
async def dashboard_summary(
    principal: Annotated[Principal, Depends(require_user)], queries: Queries
) -> DashboardSummary:
    return await queries.dashboard(UUID(principal.subject), principal.role)
