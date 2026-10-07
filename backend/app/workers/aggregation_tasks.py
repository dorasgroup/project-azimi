"""Celery tasks for run-level analysis aggregation execution."""

from __future__ import annotations

from datetime import UTC, datetime
import logging

from celery.exceptions import Retry
from uuid import UUID

from app.bootstrap.aggregation_worker import run_aggregation_worker
from app.workers.celery_app import celery_app
from app.shared.integration.consumer_ack import acknowledge_outbox, outbox_id_from_request

logger = logging.getLogger("rasad.workers.aggregation")


@celery_app.task(name="monitoring.finalize_aggregation", queue="monitoring.control", bind=True,
                 acks_late=True, reject_on_worker_lost=True, acks_on_failure_or_timeout=False, max_retries=None)
def finalize_aggregation(self, aggregation_id: str) -> dict[str, object]:
    from app.bootstrap.aggregation_worker import run_aggregation_finalization
    try:
        return run_aggregation_finalization(UUID(aggregation_id), outbox_id_from_request(self.request))
    except Exception:
        raise self.retry(exc=RuntimeError("Aggregation finalization infrastructure failure"), countdown=10, max_retries=None) from None


@celery_app.task(
    name="analysis.ensure_run_aggregation", queue="analysis.aggregate",
    bind=True, acks_late=True, reject_on_worker_lost=True, max_retries=None,
)
def ensure_run_aggregation(self, run_id_str: str) -> dict[str, str]:
    from app.bootstrap.aggregation_worker import run_aggregation_handoff

    try:
        outbox_id = outbox_id_from_request(self.request)
        if outbox_id is None:
            run_aggregation_handoff(run_id_str)
        else:
            run_aggregation_handoff(run_id_str, outbox_id)
        return {"status": "ensured", "run_id": run_id_str}
    except Exception as exc:
        raise self.retry(exc=exc, countdown=10, max_retries=None)


@celery_app.task(
    name="analysis.aggregate_run",
    bind=True,
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=None,
    acks_on_failure_or_timeout=False,
)
def process_run_aggregation(self, aggregation_id_str: str) -> dict[str, object]:
    """Executes run-level analysis aggregation with lease fencing and safe redelivery."""
    try:
        result = run_aggregation_worker(aggregation_id_str)

        if result.get("status") == "active_lease":
            lease_expires_at_raw = result.get("lease_expires_at")
            if lease_expires_at_raw:
                lease_expires_at = datetime.fromisoformat(str(lease_expires_at_raw))
                now = datetime.now(UTC)
                remaining = (lease_expires_at - now).total_seconds()
                retry_after = max(5, int(remaining) + 2)
            else:
                retry_after = 5
            logger.info("RunAggregation %s under active lease; retrying after %ss", aggregation_id_str, retry_after)
            raise self.retry(countdown=retry_after, max_retries=None)

        if result.get("status") == "already_terminal":
            logger.info("RunAggregation %s already terminal; clean no-op ACK", aggregation_id_str)
            acknowledge_outbox(self.request)
            return result

        if result.get("status") in {"completed", "failed", "run_cancelled"}:
            acknowledge_outbox(self.request)
            return result
        raise self.retry(countdown=10, max_retries=None)

    except Retry:
        raise
    except Exception:
        raise self.retry(exc=RuntimeError("Aggregation infrastructure failure"), countdown=10, max_retries=None) from None
