from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.integrations.bale.schemas import BaleUpdatePayload, BaleWebhookResult
from app.integrations.bale.service import BaleWebhookService, require_bale_webhook

router = APIRouter()


async def verify_bale_webhook(
    request: Request,
    key: Annotated[str | None, Query(max_length=256)] = None,
) -> None:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > 256 * 1024:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="Webhook payload is too large",
                )
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid Content-Length header",
            ) from None
    require_bale_webhook(key)


@router.post(
    "/webhook",
    response_model=BaleWebhookResult,
    dependencies=[Depends(verify_bale_webhook)],
    include_in_schema=False,
)
async def receive_bale_update(
    payload: BaleUpdatePayload,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> BaleWebhookResult:
    return await BaleWebhookService(session).handle(
        payload,
        request_id=request.state.request_id,
    )
