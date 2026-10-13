import asyncio
from datetime import UTC, datetime

from app.core.config import settings
from app.integrations.bale.celery_app import BALE_TASK_NAME, celery_app


class BalePublishError(RuntimeError):
    pass


async def publish_bale_update(update_id: int, request_id: str | None) -> None:
    headers = {
        "schema_version": 1,
        "request_id": request_id,
        "enqueued_at": datetime.now(UTC).isoformat(),
    }
    try:
        await asyncio.to_thread(
            celery_app.send_task,
            BALE_TASK_NAME,
            args=[update_id],
            headers=headers,
            exchange=settings.bale_exchange_name,
            routing_key=settings.bale_routing_key,
            queue=settings.bale_queue_name,
            serializer="json",
            delivery_mode=2,
            retry=False,
        )
    except Exception as exc:
        raise BalePublishError("Bale queue publication failed") from exc
