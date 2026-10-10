from celery import Celery

from app.core.config import settings


celery_producer = Celery(
    "rasad_analysis_producer",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)
