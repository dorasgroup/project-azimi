from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.api.dependencies import Pagination, Principal, pagination, require_user
from app.api.operations.monitoring import MonitoringOperations
from app.bootstrap.providers import provide_monitoring_operations
from app.schemas.common import MessageResponse, PaginatedResponse
from app.schemas.monitoring import MonitoringJobCreate, MonitoringJobRead, MonitoringJobUpdate

router = APIRouter(prefix="/monitoring/jobs", tags=["monitoring"])
Monitoring = Annotated[MonitoringOperations, Depends(provide_monitoring_operations)]


@router.post("", response_model=MonitoringJobRead, status_code=status.HTTP_202_ACCEPTED)
async def create_job(
    payload: MonitoringJobCreate,
    principal: Annotated[Principal, Depends(require_user)],
    operations: Monitoring,
) -> MonitoringJobRead:
    return await operations.create(payload, UUID(principal.subject))


@router.get("", response_model=PaginatedResponse[MonitoringJobRead])
async def list_jobs(
    principal: Annotated[Principal, Depends(require_user)],
    page_info: Annotated[Pagination, Depends(pagination)],
    operations: Monitoring,
) -> PaginatedResponse[MonitoringJobRead]:
    return await operations.list(
        page_info.page, page_info.page_size, UUID(principal.subject), principal.role
    )


@router.get("/{job_id}", response_model=MonitoringJobRead)
async def get_job(
    job_id: UUID,
    principal: Annotated[Principal, Depends(require_user)],
    operations: Monitoring,
) -> MonitoringJobRead:
    return await operations.get(job_id, UUID(principal.subject), principal.role)


@router.put("/{job_id}", response_model=MonitoringJobRead)
async def update_job(
    job_id: UUID,
    payload: MonitoringJobUpdate,
    principal: Annotated[Principal, Depends(require_user)],
    operations: Monitoring,
) -> MonitoringJobRead:
    return await operations.update(job_id, payload, UUID(principal.subject), principal.role)


@router.post("/{job_id}/retry", response_model=MessageResponse)
async def retry_job(
    job_id: UUID,
    principal: Annotated[Principal, Depends(require_user)],
    operations: Monitoring,
) -> MessageResponse:
    await operations.retry(job_id, UUID(principal.subject), principal.role)
    return MessageResponse(message="Monitoring job retried")


@router.post("/{job_id}/cancel", response_model=MessageResponse)
async def cancel_job(
    job_id: UUID,
    principal: Annotated[Principal, Depends(require_user)],
    operations: Monitoring,
) -> MessageResponse:
    await operations.cancel(job_id, UUID(principal.subject), principal.role)
    return MessageResponse(message="Monitoring job cancellation requested")


@router.delete("/{job_id}", response_model=MessageResponse)
async def delete_job(
    job_id: UUID,
    principal: Annotated[Principal, Depends(require_user)],
    operations: Monitoring,
) -> MessageResponse:
    await operations.delete(job_id, UUID(principal.subject), principal.role)
    return MessageResponse(message="Monitoring job deleted")
