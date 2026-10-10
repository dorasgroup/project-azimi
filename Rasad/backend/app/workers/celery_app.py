from __future__ import annotations

import os
from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "rasad_analysis",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

worker_name = os.environ.get("CELERY_WORKER_NAME", "")
is_source_worker = worker_name == "source-worker"
is_analysis_worker = worker_name == "analysis-worker"
is_control_worker = worker_name == "control-worker"
is_aggregation_worker = worker_name == "aggregation-worker"

if is_source_worker:
    active_visibility_timeout = settings.celery_source_visibility_timeout_seconds
elif is_analysis_worker:
    active_visibility_timeout = settings.celery_analysis_visibility_timeout_seconds
elif worker_name == "report-worker":
    active_visibility_timeout = max(settings.celery_visibility_timeout, 900)
elif is_aggregation_worker:
    active_visibility_timeout = max(settings.celery_visibility_timeout, 1200)
else:
    active_visibility_timeout = settings.celery_visibility_timeout

celery_app.conf.update(
    task_track_started=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    worker_prefetch_multiplier=1,
    broker_transport_options={"visibility_timeout": active_visibility_timeout},
    result_backend_transport_options={"visibility_timeout": active_visibility_timeout},
    task_routes={
        "monitoring.process_source_task": {"queue": "monitoring.source"},
        "analysis.process_source_analysis": {"queue": "analysis.source"},
        "monitoring.process_control_event": {"queue": "monitoring.control"},
        "analysis.aggregate_run": {"queue": "analysis.aggregate"},
        "analysis.ensure_run_aggregation": {"queue": "analysis.aggregate"},
        "analysis.ensure_source_analysis": {"queue": "analysis.source"},
        "report.ensure_generation": {"queue": "report.generate"},
        "report.generate_report": {"queue": "report.generate"},
        "monitoring.finalize_report": {"queue": "monitoring.control"},
        "monitoring.finalize_aggregation": {"queue": "monitoring.control"},
    },
)
celery_app.autodiscover_tasks(["app.workers"])

from app.workers import analysis_tasks as _analysis_tasks  # noqa: E402,F401
from app.workers import crawler_tasks as _crawler_tasks  # noqa: E402,F401
from app.workers import monitoring_tasks as _monitoring_tasks  # noqa: E402,F401
from app.workers import processing_tasks as _processing_tasks  # noqa: E402,F401
from app.workers import source_tasks as _source_tasks  # noqa: E402,F401
from app.workers import control_tasks as _control_tasks  # noqa: E402,F401
from app.workers import aggregation_tasks as _aggregation_tasks  # noqa: E402,F401
from app.workers import report_tasks as _report_tasks  # noqa: E402,F401


@celery_app.task(name="system.ping")
def ping() -> str:
    return "pong"
