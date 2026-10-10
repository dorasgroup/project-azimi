"""Celery transport adapter for monitoring workflows."""

from app.bootstrap.monitoring_worker import process_monitoring_job
from app.workers.celery_app import celery_app


@celery_app.task(
    name="monitoring.process_job",
    acks_late=True,
    reject_on_worker_lost=True,
)
def process_job(job_id: str) -> dict:
    return process_monitoring_job(job_id)
