"""Celery transport adapter for individual source task execution."""

from __future__ import annotations

from datetime import UTC, datetime
import logging

from celery.exceptions import Retry

from app.bootstrap.monitoring_worker import run_source_task_worker
from app.workers.celery_app import celery_app
from app.shared.integration.consumer_ack import acknowledge_outbox

logger = logging.getLogger("rasad.workers.source")


@celery_app.task(
    bind=True,
    name="monitoring.process_source_task",
    queue="monitoring.source",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=3,
    default_retry_delay=5,
)
def process_source_task(self, source_task_id: str) -> dict:
    celery_retry = getattr(self.request, "retries", 0)
    logger.info(
        "Celery process_source_task started for %s (celery_retry_count=%d)",
        source_task_id,
        celery_retry,
    )
    try:
        result = run_source_task_worker(source_task_id)
        status = result.get("status")

        if status == "active_lease":
            # Worker crash redelivery or duplicate delivery while DB lease is still actively held.
            # Do NOT acknowledge away the task! Delay retry until lease expiration.
            lease_exp_str = result.get("lease_expires_at")
            safety_margin = 2
            minimum_retry_delay = 5
            retry_after = minimum_retry_delay

            if lease_exp_str:
                try:
                    lease_expires_at = datetime.fromisoformat(lease_exp_str)
                    if lease_expires_at.tzinfo is None:
                        lease_expires_at = lease_expires_at.replace(tzinfo=UTC)
                    remaining = (lease_expires_at - datetime.now(UTC)).total_seconds()
                    retry_after = max(minimum_retry_delay, int(remaining) + safety_margin)
                except Exception as parse_err:
                    logger.warning("Could not parse lease_expires_at '%s': %s", lease_exp_str, parse_err)

            logger.warning(
                "Active lease held for source task %s. Scheduling delayed retry in %ds (celery_retry=%d)",
                source_task_id,
                retry_after,
                celery_retry,
            )
            # Separate retry budget for ownership contention (max_retries=10)
            raise self.retry(countdown=retry_after, max_retries=10)

        if status == "already_terminal":
            logger.info(
                "Source task %s is already terminal; acknowledging without duplicate execution",
                source_task_id,
            )
            acknowledge_outbox(self.request)
            return result

        logger.info(
            "Celery process_source_task completed for %s with status=%s",
            source_task_id,
            status,
        )
        if status in {"completed", "failed", "run_cancelled", "continuation_queued"}:
            acknowledge_outbox(self.request)
        return result
    except Retry:
        raise
    except Exception as exc:
        logger.error(
            "Unexpected error in process_source_task %s: %s",
            source_task_id,
            exc,
        )
        raise
