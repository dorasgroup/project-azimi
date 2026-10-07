from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from app.api.dependencies import Pagination, Principal, pagination, require_user
from app.api.operations.reports import ReportOperations
from app.bootstrap.providers import provide_report_operations
from app.schemas.common import MessageResponse, PaginatedResponse
from app.schemas.report import ReportRead

router = APIRouter(prefix="/reports", tags=["reports"])
Reports = Annotated[ReportOperations, Depends(provide_report_operations)]


@router.get("", response_model=PaginatedResponse[ReportRead])
async def list_reports(
    principal: Annotated[Principal, Depends(require_user)],
    page_info: Annotated[Pagination, Depends(pagination)],
    operations: Reports,
) -> PaginatedResponse[ReportRead]:
    return await operations.list(
        page_info.page, page_info.page_size, UUID(principal.subject), principal.role
    )


@router.get("/{report_id}", response_model=ReportRead)
async def get_report(
    report_id: UUID,
    principal: Annotated[Principal, Depends(require_user)],
    operations: Reports,
) -> ReportRead:
    return await operations.get(report_id, UUID(principal.subject), principal.role)


@router.delete("/{report_id}", response_model=MessageResponse)
async def delete_report(
    report_id: UUID,
    principal: Annotated[Principal, Depends(require_user)],
    operations: Reports,
) -> MessageResponse:
    await operations.delete(report_id, UUID(principal.subject), principal.role)
    return MessageResponse(message="Report deleted")


@router.get("/{report_id}/download/pdf", response_class=FileResponse)
async def download_pdf(
    report_id: UUID,
    principal: Annotated[Principal, Depends(require_user)],
    operations: Reports,
) -> FileResponse:
    path = await operations.export(report_id, "pdf", UUID(principal.subject), principal.role)
    return FileResponse(path, media_type="application/pdf", filename=f"{report_id}.pdf")


@router.get("/{report_id}/download/docx", response_class=FileResponse)
async def download_docx(
    report_id: UUID,
    principal: Annotated[Principal, Depends(require_user)],
    operations: Reports,
) -> FileResponse:
    path = await operations.export(report_id, "docx", UUID(principal.subject), principal.role)
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=f"{report_id}.docx",
    )
