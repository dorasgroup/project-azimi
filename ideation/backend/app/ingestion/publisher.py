import asyncio
from datetime import UTC, datetime
from time import perf_counter
from uuid import UUID

from app.core.config import settings
from app.ingestion.celery_app import INGESTION_TASK_NAME, celery_app
from app.middleware.request_logging import valid_request_id
from app.observability.metrics import INGESTION_PUBLISH_DURATION


class IngestionPublishError(Exception):
    pass


async def publish_ingestion_job(job_id: UUID, request_id: str | None) -> None:
    safe_request_id = request_id if valid_request_id(request_id) else None
    headers = {
        "schema_version": 1,
        "request_id": safe_request_id,
        "enqueued_at": datetime.now(UTC).isoformat(),
    }
    started = perf_counter()
    try:
        await asyncio.to_thread(
            celery_app.send_task,
            INGESTION_TASK_NAME,
            args=[str(job_id)],
            headers=headers,
            exchange=settings.ingestion_exchange_name,
            routing_key=settings.ingestion_routing_key,
            queue=settings.ingestion_queue_name,
            serializer="json",
            delivery_mode=2,
            retry=False,
        )
    except Exception as exc:
        raise IngestionPublishError("Ingestion queue publication failed") from exc
    finally:
        INGESTION_PUBLISH_DURATION.observe(perf_counter() - started)
