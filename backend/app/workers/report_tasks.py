"""Transport/retry adapters; report orchestration and persistence are application work."""

from datetime import UTC, datetime
from uuid import UUID
from celery.exceptions import Retry
from app.bootstrap.report_worker import run_report_finalization, run_report_generation, run_report_handoff
from app.shared.integration.consumer_ack import acknowledge_outbox, outbox_id_from_request
from app.workers.celery_app import celery_app


@celery_app.task(name="report.ensure_generation", queue="report.generate", bind=True, acks_late=True, reject_on_worker_lost=True, acks_on_failure_or_timeout=False, max_retries=None)
def ensure_generation(self, payload: dict[str, object]) -> dict[str, object]:
    run_id, result_id = UUID(str(payload["run_id"])), UUID(str(payload["result_id"]))
    try:
        return run_report_handoff(run_id, result_id, outbox_id_from_request(self.request))
    except Exception:
        raise self.retry(exc=RuntimeError("Report handoff infrastructure failure"), countdown=10, max_retries=None) from None


@celery_app.task(name="report.generate_report", queue="report.generate", bind=True, acks_late=True, reject_on_worker_lost=True, acks_on_failure_or_timeout=False, max_retries=None, soft_time_limit=240, time_limit=300)
def generate_report(self, generation_id: str) -> dict[str, object]:
    identifier = UUID(generation_id)
    try:
        result = run_report_generation(identifier)
        if result["status"] in {"completed", "failed", "run_cancelled", "already_terminal"}:
            acknowledge_outbox(self.request)
            return result
        countdown = 10
        if result["status"] == "active_lease" and result.get("lease_expires_at"):
            countdown = max(5, int((datetime.fromisoformat(str(result["lease_expires_at"])) - datetime.now(UTC)).total_seconds()) + 2)
        raise self.retry(countdown=countdown, max_retries=None)
    except Retry:
        raise
    except Exception:
        raise self.retry(exc=RuntimeError("Report generation infrastructure failure"), countdown=10, max_retries=None) from None


@celery_app.task(name="monitoring.finalize_report", queue="monitoring.control", bind=True, acks_late=True, reject_on_worker_lost=True, acks_on_failure_or_timeout=False, max_retries=None)
def finalize_report(self, generation_id: str) -> dict[str, object]:
    identifier = UUID(generation_id)
    try:
        return run_report_finalization(identifier, outbox_id_from_request(self.request))
    except Exception:
        raise self.retry(exc=RuntimeError("Report finalization infrastructure failure"), countdown=10, max_retries=None) from None
