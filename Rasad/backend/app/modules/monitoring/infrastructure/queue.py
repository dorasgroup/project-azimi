"""Celery queue adapter for persisted monitoring jobs."""

from uuid import UUID


class CeleryMonitoringJobQueue:
    """Keeps Celery details outside application use cases."""

    def enqueue(self, job_id: UUID) -> None:
        from app.workers.producer import celery_producer

        celery_producer.send_task("monitoring.process_job", args=[str(job_id)])
