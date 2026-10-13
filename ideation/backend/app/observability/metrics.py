import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from functools import wraps
from pathlib import Path
from time import perf_counter

import httpx
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    multiprocess,
)
from starlette.responses import Response

HTTP_REQUESTS = Counter(
    "ideation_http_requests_total",
    "Completed HTTP requests.",
    ("method", "route", "status_class"),
)
HTTP_DURATION = Histogram(
    "ideation_http_request_duration_seconds",
    "HTTP request duration excluding health and metrics endpoints.",
    ("method", "route"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)
HTTP_IN_PROGRESS = Gauge(
    "ideation_http_requests_in_progress",
    "HTTP requests currently executing.",
    ("method",),
    multiprocess_mode="livesum",
)
HTTP_UNHANDLED_EXCEPTIONS = Counter(
    "ideation_http_unhandled_exceptions_total",
    "Unhandled application exceptions.",
    ("method", "route", "exception_type"),
)

UPLOAD_RECEIVED = Counter(
    "ideation_upload_received_total",
    "Upload requests accepted by the service layer.",
)
UPLOAD_RESULTS = Counter(
    "ideation_upload_results_total",
    "Upload request results.",
    ("outcome",),
)
UPLOAD_VALIDATION_DURATION = Histogram(
    "ideation_upload_validation_duration_seconds",
    "Upload validation duration.",
    buckets=(0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1),
)
UPLOAD_STORAGE_DURATION = Histogram(
    "ideation_upload_storage_duration_seconds",
    "Upload streaming and local storage duration.",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)
UPLOAD_INDEXING_DURATION = Histogram(
    "ideation_upload_indexing_duration_seconds",
    "Dify indexing duration for new non-audio Uploads.",
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 180),
)
UPLOAD_SIZE = Histogram(
    "ideation_upload_size_bytes",
    "Accepted Upload size in bytes.",
    buckets=(1024, 10_240, 102_400, 1_048_576, 10_485_760, 52_428_800, 104_857_600, 524_288_000),
)
UPLOAD_INDEXING_RESULTS = Counter(
    "ideation_upload_indexing_results_total",
    "New Upload indexing outcomes.",
    ("outcome",),
)

CHAT_REQUESTS = Counter(
    "ideation_chat_requests_total",
    "Chat service requests.",
)
CHAT_RESULTS = Counter(
    "ideation_chat_results_total",
    "Chat service results.",
    ("outcome",),
)
CHAT_DURATION = Histogram(
    "ideation_chat_duration_seconds",
    "End-to-end Chat service duration.",
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 8, 10, 15, 30, 60, 120),
)
CHAT_RETRIEVAL_DURATION = Histogram(
    "ideation_chat_retrieval_duration_seconds",
    "Chat retrieval duration.",
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)
CHAT_MODEL_DURATION = Histogram(
    "ideation_chat_model_duration_seconds",
    "Chat model/Dify call duration.",
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 8, 10, 15, 30, 60, 120),
)
CHAT_WITH_SOURCES = Counter(
    "ideation_chat_responses_with_sources_total",
    "Successful Chat responses containing at least one source.",
)

DEPENDENCY_REQUESTS = Counter(
    "ideation_dependency_requests_total",
    "AI dependency call results.",
    ("dependency", "operation", "outcome"),
)
DEPENDENCY_DURATION = Histogram(
    "ideation_dependency_request_duration_seconds",
    "AI dependency call duration.",
    ("dependency", "operation"),
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 180),
)
DEPENDENCY_TIMEOUTS = Counter(
    "ideation_dependency_timeouts_total",
    "AI dependency timeouts.",
    ("dependency", "operation"),
)
AI_FALLBACKS = Counter(
    "ideation_ai_fallbacks_total",
    "Controlled AI fallback transitions.",
    ("from_dependency", "to_dependency", "reason"),
)

INGESTION_JOBS_CREATED = Counter(
    "ideation_ingestion_jobs_created_total",
    "Ingestion jobs created after a stored Upload.",
)
INGESTION_TRANSITIONS = Counter(
    "ideation_ingestion_job_transitions_total",
    "Accepted ingestion job state transitions.",
    ("status",),
)
INGESTION_ENQUEUE_FAILURES = Counter(
    "ideation_ingestion_enqueue_failures_total",
    "RabbitMQ publication failures recorded as recoverable.",
)
INGESTION_RETRY_ATTEMPTS = Counter(
    "ideation_ingestion_retry_attempts_total",
    "Automatic or explicit ingestion retry attempts.",
    ("kind",),
)
INGESTION_PUBLISH_DURATION = Histogram(
    "ideation_ingestion_publish_duration_seconds",
    "Confirmed RabbitMQ publication duration.",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5),
)
INGESTION_TASK_DURATION = Histogram(
    "ideation_ingestion_worker_task_duration_seconds",
    "End-to-end ingestion task execution duration.",
    buckets=(0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 180, 240),
)
INGESTION_TASKS_IN_PROGRESS = Gauge(
    "ideation_ingestion_worker_tasks_in_progress",
    "Ingestion tasks currently executing.",
    multiprocess_mode="livesum",
)
INGESTION_DIFY_DURATION = Histogram(
    "ideation_ingestion_dify_indexing_duration_seconds",
    "Dify indexing duration observed by the ingestion worker.",
    buckets=(0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 180),
)
INGESTION_WORKER_FAILURES = Counter(
    "ideation_ingestion_worker_failures_total",
    "Classified ingestion worker failures.",
    ("error_type",),
)
INGESTION_WORKER_ACTIVE = Gauge(
    "ideation_ingestion_worker_active",
    "Whether an ingestion worker main process is active.",
    multiprocess_mode="livesum",
)

EXCLUDED_HTTP_PATHS = {
    "/metrics",
    "/health/live",
    "/api/v1/health",
    "/api/v1/health/live",
    "/api/v1/health/ready",
    "/api/v1/health/dify",
}

_DEPENDENCIES = {"dify", "weaviate"}
_OPERATIONS = {
    "chat",
    "create_document",
    "delete_document",
    "get_document",
    "health",
    "list_documents",
    "retrieve",
}


def bounded_exception_type(exc: BaseException) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        return "http_status"
    if isinstance(exc, httpx.TransportError):
        return "transport_error"
    return "internal"


@asynccontextmanager
async def observe_dependency(dependency: str, operation: str) -> AsyncIterator[None]:
    if dependency not in _DEPENDENCIES or operation not in _OPERATIONS:
        raise ValueError("Unbounded dependency metric label")
    started = perf_counter()
    outcome = "success"
    try:
        yield
    except httpx.TimeoutException:
        outcome = "timeout"
        DEPENDENCY_TIMEOUTS.labels(dependency, operation).inc()
        raise
    except httpx.HTTPStatusError:
        outcome = "http_error"
        raise
    except httpx.TransportError:
        outcome = "transport_error"
        raise
    finally:
        DEPENDENCY_REQUESTS.labels(dependency, operation, outcome).inc()
        DEPENDENCY_DURATION.labels(dependency, operation).observe(perf_counter() - started)


def record_fallback(from_dependency: str, to_dependency: str, reason: str) -> None:
    allowed = {
        ("weaviate", "dify", "empty"),
        ("weaviate", "dify", "http_error"),
        ("weaviate", "dify", "response_error"),
        ("weaviate", "dify", "timeout"),
        ("weaviate", "dify", "transport_error"),
        ("dify", "knowledge_base", "recoverable_http_error"),
    }
    labels = (from_dependency, to_dependency, reason)
    if labels not in allowed:
        raise ValueError("Unbounded fallback metric label")
    AI_FALLBACKS.labels(*labels).inc()


def observe_chat(func: Callable):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        CHAT_REQUESTS.inc()
        started = perf_counter()
        try:
            result = await func(*args, **kwargs)
        except Exception:
            CHAT_RESULTS.labels("failure").inc()
            raise
        else:
            CHAT_RESULTS.labels("success").inc()
            if getattr(result, "sources", None):
                CHAT_WITH_SOURCES.inc()
            return result
        finally:
            CHAT_DURATION.observe(perf_counter() - started)

    return wrapper


def observe_upload(func: Callable):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        UPLOAD_RECEIVED.inc()
        try:
            result = await func(*args, **kwargs)
        except Exception:
            UPLOAD_RESULTS.labels("failure").inc()
            raise
        outcome = "duplicate" if result.message.startswith("This file already") else "success"
        UPLOAD_RESULTS.labels(outcome).inc()
        return result

    return wrapper


def metrics_response() -> Response:
    if os.getenv("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
    else:
        registry = REGISTRY
    return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)


def prepare_multiprocess_directory() -> None:
    configured = os.getenv("PROMETHEUS_MULTIPROC_DIR")
    if not configured:
        return
    directory = Path(configured).resolve()
    if not directory.is_absolute() or directory == Path("/"):
        raise ValueError("PROMETHEUS_MULTIPROC_DIR must be a dedicated absolute directory")
    directory.mkdir(parents=True, exist_ok=True)
    for metric_file in directory.glob("*.db"):
        metric_file.unlink()


def mark_current_process_dead() -> None:
    if os.getenv("PROMETHEUS_MULTIPROC_DIR"):
        multiprocess.mark_process_dead(os.getpid())
