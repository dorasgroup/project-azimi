"""Celery transport adapter for durable monitoring control events."""

from __future__ import annotations

import logging

from app.bootstrap.control_worker import run_control_event_worker
from app.modules.monitoring.application.control_event import ControlOutcomeKind
from app.modules.monitoring.ports.completion import TransientControlInfrastructureError
from app.workers.celery_app import celery_app
from app.workers.control_retry_policy import calculate_retry_backoff
from app.shared.integration.consumer_ack import outbox_id_from_request

logger = logging.getLogger("rasad.workers.control")
ESCALATION_RETRY_THRESHOLDS = {5, 10, 20, 50, 100}


@celery_app.task(
    name="monitoring.process_control_event",
    bind=True,
    acks_late=True,
    reject_on_worker_lost=True,
    acks_on_failure_or_timeout=False,
    max_retries=None,
)
def process_control_event(self, payload: dict[str, object]) -> dict[str, object]:
    """Map application outcomes to Celery retry and acknowledgement semantics."""
    if not isinstance(payload, dict):
        raise ValueError("Control event payload must be an object")

    try:
        outbox_id = outbox_id_from_request(self.request)
        outcome = (
            run_control_event_worker(payload, outbox_id)
            if outbox_id is not None else run_control_event_worker(payload)
        )
    except TransientControlInfrastructureError as exc:
        return _retry_transient(self, payload, exc)
    except Exception:
        logger.exception("Unknown control event failure; task must not acknowledge success")
        raise

    if outcome.kind == ControlOutcomeKind.RETRY_AT:
        retry = self.retry(countdown=outcome.retry_after, max_retries=None)
        if isinstance(retry, BaseException):
            raise retry
        return outcome.payload
    if outcome.kind == ControlOutcomeKind.RETRY_TRANSIENT:
        return _retry_transient(self, payload)
    if outcome.kind == ControlOutcomeKind.PERMANENT_FAILURE:
        logger.error("Permanent control event failure: %s", outcome.payload)
    # Relay-originated control ACK is part of the evaluator's business commit.
    # Legacy messages have no outbox row to acknowledge.
    return outcome.payload


def _retry_transient(self, payload: dict[str, object], exc: Exception | None = None):
    retries_done = getattr(self.request, "retries", 0)
    backoff = calculate_retry_backoff(retries_done)
    if retries_done in ESCALATION_RETRY_THRESHOLDS:
        logger.error(
            "ESCALATION: Persistent transient control failure for run %s, attempt %s",
            payload.get("run_id"), retries_done + 1,
        )
    else:
        logger.warning(
            "Transient control failure for run %s, attempt %s; retry in %ss",
            payload.get("run_id"), retries_done + 1, backoff,
        )
    retry = self.retry(exc=exc, countdown=backoff, max_retries=None)
    if isinstance(retry, BaseException):
        raise retry
    return {"status": "retry_scheduled", "run_id": str(payload.get("run_id")),
            "attempt": retries_done + 1, "countdown": backoff}
