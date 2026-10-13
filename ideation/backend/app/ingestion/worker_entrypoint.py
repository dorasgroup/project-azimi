import os
from pathlib import Path

from prometheus_client import CollectorRegistry, multiprocess, start_http_server


def main() -> None:
    metrics_path = Path(os.environ.get("PROMETHEUS_MULTIPROC_DIR", "/tmp/worker-metrics"))
    if not metrics_path.is_absolute() or metrics_path == Path("/"):
        raise ValueError("Worker metrics directory must be a dedicated absolute path")
    metrics_path.mkdir(parents=True, exist_ok=True)
    os.chown(metrics_path, 65534, 65534)
    metrics_path.chmod(0o770)
    for metric_file in metrics_path.glob("*.db"):
        metric_file.unlink()

    # The API and worker deliberately run with different Unix identities.  Do not
    # let the worker's rotating file handlers contend with API-owned log files on
    # the shared document volume.
    worker_log_path = Path("/tmp/ideation-worker-logs")
    worker_log_path.mkdir(parents=True, exist_ok=True)
    os.chown(worker_log_path, 65534, 65534)
    worker_log_path.chmod(0o750)
    os.environ["LOG_DIR"] = str(worker_log_path)

    from app.core.logging import setup_logging
    from app.ingestion import tasks as ingestion_tasks
    from app.ingestion.celery_app import celery_app
    from app.observability.metrics import INGESTION_WORKER_ACTIVE

    # Import task modules while the root entrypoint can read the Docker secret.
    # Prefork children inherit the initialized broker configuration after Celery
    # drops them to the unprivileged worker identity.
    del ingestion_tasks

    setup_logging()
    for log_file in worker_log_path.glob("*.log"):
        os.chown(log_file, 65534, 65534)
        log_file.chmod(0o640)
    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry)
    INGESTION_WORKER_ACTIVE.set(1)
    start_http_server(9100, addr="0.0.0.0", registry=registry)
    celery_app.worker_main(
        argv=[
            "worker",
            "--loglevel=INFO",
            "--concurrency=1",
            "--queues=ideation.ingestion.jobs.v1",
            "--hostname=ingestion@%h",
            "--uid=nobody",
            "--gid=nogroup",
        ]
    )


if __name__ == "__main__":
    main()
