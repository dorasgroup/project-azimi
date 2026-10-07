from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import Pagination, Principal, pagination, require_admin
from app.api.operations.observability import QueryOperations
from app.bootstrap.providers import provide_query_operations
from app.shared.domain.logging import LogLevel
from app.schemas.common import MessageResponse, PaginatedResponse
from app.schemas.system_log import SystemLogRead

router = APIRouter(prefix="/system/logs", tags=["system-logs"])
Queries = Annotated[QueryOperations, Depends(provide_query_operations)]


@router.get("", response_model=PaginatedResponse[SystemLogRead])
async def list_system_logs(
    _: Annotated[Principal, Depends(require_admin)],
    page_info: Annotated[Pagination, Depends(pagination)],
    queries: Queries,
    level: Annotated[LogLevel | None, Query()] = None,
    module: Annotated[str | None, Query(max_length=250)] = None,
) -> PaginatedResponse[SystemLogRead]:
    return await queries.logs(page_info.page, page_info.page_size, level, module)


@router.delete("/resolved", response_model=MessageResponse)
async def delete_resolved_logs(
    _: Annotated[Principal, Depends(require_admin)], queries: Queries
) -> MessageResponse:
    deleted = await queries.delete_resolved_logs()
    return MessageResponse(message=f"{deleted} resolved logs deleted")
