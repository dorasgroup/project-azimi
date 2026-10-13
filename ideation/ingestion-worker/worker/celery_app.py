import os
from celery import Celery
from kombu import Exchange, Queue

broker_url = os.environ.get("CELERY_BROKER_URL", "amqp://guest:guest@localhost:5672//")
result_backend = os.environ.get("CELERY_RESULT_BACKEND", "rpc://")

celery_app = Celery(
    "ingestion_worker",
    broker=broker_url,
    backend=result_backend,
    include=["worker.tasks"]
)

default_exchange = Exchange('ingestion', type='direct')
dlq_exchange = Exchange('ingestion.dlq', type='direct')

celery_app.conf.task_queues = (
    Queue(
        'ingestion.jobs',
        default_exchange,
        routing_key='job.process',
        queue_arguments={
            'x-dead-letter-exchange': 'ingestion.dlq',
            'x-dead-letter-routing-key': 'job.dlq'
        }
    ),
    Queue('ingestion.dlq.queue', dlq_exchange, routing_key='job.dlq'),
)

celery_app.conf.update(
    task_default_queue='ingestion.jobs',
    task_default_exchange='ingestion',
    task_default_routing_key='job.process',
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True, # Prevent data loss
    worker_prefetch_multiplier=1,
)
