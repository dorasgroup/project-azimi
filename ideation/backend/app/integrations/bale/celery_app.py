from celery import Celery
from kombu import Exchange, Queue

from app.core.config import settings

BALE_TASK_NAME = "ideation.bale.process.v1"

celery_app = Celery(
    "ideation_bale",
    broker=settings.celery_broker_url,
    backend=None,
    include=["app.integrations.bale.tasks"],
)

exchange = Exchange(settings.bale_exchange_name, type="direct", durable=True)
celery_app.conf.update(
    accept_content=["json"],
    broker_connection_retry=True,
    broker_connection_retry_on_startup=True,
    broker_heartbeat=30,
    enable_utc=True,
    result_backend=None,
    task_acks_late=True,
    task_default_delivery_mode="persistent",
    task_default_exchange=settings.bale_exchange_name,
    task_default_queue=settings.bale_queue_name,
    task_default_routing_key=settings.bale_routing_key,
    task_ignore_result=True,
    task_reject_on_worker_lost=True,
    task_serializer="json",
    task_soft_time_limit=210,
    task_time_limit=240,
    task_queues=(
        Queue(
            settings.bale_queue_name,
            exchange=exchange,
            routing_key=settings.bale_routing_key,
            durable=True,
        ),
    ),
    timezone="UTC",
    worker_hijack_root_logger=False,
    worker_prefetch_multiplier=1,
)
