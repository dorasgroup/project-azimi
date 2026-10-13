from celery import Celery
from kombu import Exchange, Queue

from app.core.config import settings

INGESTION_TASK_NAME = "ideation.ingestion.process.v1"

celery_app = Celery(
    "ideation_ingestion",
    broker=settings.celery_broker_url,
    backend=None,
    include=["app.ingestion.tasks"],
)

exchange = Exchange(settings.ingestion_exchange_name, type="direct", durable=True)
celery_app.conf.update(
    accept_content=["json"],
    broker_connection_timeout=3,
    broker_connection_retry=True,
    broker_connection_retry_on_startup=True,
    broker_connection_max_retries=None,
    broker_heartbeat=30,
    broker_transport_options={
        "confirm_publish": True,
        "max_retries": 2,
        "interval_start": 0,
        "interval_step": 0.2,
        "interval_max": 0.5,
    },
    enable_utc=True,
    result_backend=None,
    task_acks_late=True,
    task_acks_on_failure_or_timeout=True,
    task_default_delivery_mode="persistent",
    task_default_exchange=settings.ingestion_exchange_name,
    task_default_queue=settings.ingestion_queue_name,
    task_default_routing_key=settings.ingestion_routing_key,
    task_ignore_result=True,
    task_reject_on_worker_lost=True,
    task_serializer="json",
    task_soft_time_limit=210,
    task_time_limit=240,
    task_track_started=False,
    task_queues=(
        Queue(
            settings.ingestion_queue_name,
            exchange=exchange,
            routing_key=settings.ingestion_routing_key,
            durable=True,
        ),
    ),
    timezone="UTC",
    worker_cancel_long_running_tasks_on_connection_loss=True,
    worker_enable_prefetch_count_reduction=True,
    worker_hijack_root_logger=False,
    worker_prefetch_multiplier=1,
)
