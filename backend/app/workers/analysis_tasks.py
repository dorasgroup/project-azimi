"""Celery transport adapter for individual source analysis execution."""

from __future__ import annotations

from datetime import UTC, datetime
import logging

from celery.exceptions import Retry

from app.bootstrap.analysis_worker import run_source_analysis_worker
from app.modules.analysis.domain.source_analysis import TransientAnalysisError
from app.workers.celery_app import celery_app
from app.shared.integration.consumer_ack import acknowledge_outbox, outbox_id_from_request

logger = logging.getLogger("rasad.workers.analysis")


@celery_app.task(
    name="analysis.ensure_source_analysis", queue="analysis.source",
    bind=True, acks_late=True, reject_on_worker_lost=True, max_retries=None,
)
def ensure_source_analysis(self, source_task_id: str) -> dict[str, str]:
    """Consume the durable source-completion obligation; creation is idempotent."""
    from app.bootstrap.analysis_worker import run_source_analysis_handoff

    try:
        outbox_id = outbox_id_from_request(self.request)
        if outbox_id is None:
            run_source_analysis_handoff(source_task_id)
        else:
            run_source_analysis_handoff(source_task_id, outbox_id)
        return {"status": "ensured", "source_task_id": source_task_id}
    except Exception as exc:
        raise self.retry(exc=exc, countdown=10, max_retries=None)


@celery_app.task(
    bind=True,
    name="analysis.process_source_analysis",
    queue="analysis.source",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=None,
    acks_on_failure_or_timeout=False,
    default_retry_delay=5,
)
def process_source_analysis(self, source_analysis_id: str) -> dict:
    celery_retry = getattr(self.request, "retries", 0)
    logger.info(
        "Celery process_source_analysis started for %s (celery_retry_count=%d)",
        source_analysis_id,
        celery_retry,
    )
    try:
        result = run_source_analysis_worker(source_analysis_id)
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
                "Active lease held for analysis %s. Scheduling delayed retry in %ds (celery_retry=%d)",
                source_analysis_id,
                retry_after,
                celery_retry,
            )
            # Separate retry budget for ownership contention (max_retries=10)
            raise self.retry(countdown=retry_after, max_retries=None)

        if status == "already_terminal":
            logger.info(
                "Analysis %s is already terminal; acknowledging without duplicate execution",
                source_analysis_id,
            )
        if status in {"completed", "failed", "run_cancelled", "already_terminal"}:
            acknowledge_outbox(self.request)
            return result
        raise self.retry(countdown=5, max_retries=None)
    except Retry:
        raise
    except TransientAnalysisError as exc:
        logger.warning(
            "TransientAnalysisError in %s: %s (transport retry %d)",
            source_analysis_id,
            type(exc).__name__,
            celery_retry + 1,
        )
        countdown = min(60, 5 * (2 ** celery_retry))
        raise self.retry(exc=RuntimeError("Temporary source analysis failure"), countdown=countdown, max_retries=None) from None
    except Exception as exc:
        logger.error(
            "Unexpected error in process_source_analysis %s: %s",
            source_analysis_id,
            type(exc).__name__,
        )
        raise self.retry(exc=RuntimeError("Source analysis infrastructure failure"), countdown=10, max_retries=None) from None
