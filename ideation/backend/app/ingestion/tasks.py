import asyncio
import logging
import secrets
from time import perf_counter
from uuid import UUID

from celery import Task

from app.core.logging import log_event
from app.ingestion.celery_app import INGESTION_TASK_NAME, celery_app
from app.ingestion.service import process_ingestion_job
from app.ingestion.state import JobStatus
from app.middleware.request_logging import valid_request_id
from app.observability.context import reset_request_id, set_request_id
from app.observability.metrics import (
    INGESTION_TASK_DURATION,
    INGESTION_TASKS_IN_PROGRESS,
    INGESTION_WORKER_FAILURES,
)

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name=INGESTION_TASK_NAME,
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=2,
    ignore_result=True,
)
def process_ingestion_task(self: Task, job_id: str) -> None:
    request_headers = getattr(self.request, "headers", {}) or {}
    supplied_request_id = request_headers.get("request_id")
    request_id = supplied_request_id if valid_request_id(supplied_request_id) else None
    context_token = set_request_id(request_id)
    started = perf_counter()
    INGESTION_TASKS_IN_PROGRESS.inc()
    try:
        try:
            parsed_job_id = UUID(job_id)
        except (TypeError, ValueError):
            INGESTION_WORKER_FAILURES.labels("invalid_job").inc()
            log_event(
                logger,
                logging.ERROR,
                "ingestion_task_rejected",
                outcome="invalid_job",
                error_type="invalid_job",
            )
            return
        delivery_info = getattr(self.request, "delivery_info", {}) or {}
        outcome = asyncio.run(
            process_ingestion_job(
                parsed_job_id,
                redelivered=bool(delivery_info.get("redelivered")),
            )
        )
        log_event(logger, logging.INFO, "ingestion_task_completed", outcome=outcome)
        if outcome == JobStatus.RETRYING:
            retries = int(getattr(self.request, "retries", 0))
            countdown = min(120, 15 * (2**retries)) + secrets.randbelow(6)
            raise self.retry(countdown=countdown)
    finally:
        INGESTION_TASKS_IN_PROGRESS.dec()
        INGESTION_TASK_DURATION.observe(perf_counter() - started)
        reset_request_id(context_token)
