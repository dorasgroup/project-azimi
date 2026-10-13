from pydantic import SecretStr

from app.core.config import settings
from app.core.logging import setup_logging
from app.integrations.bale.celery_app import celery_app


def preload_bale_bot_token() -> None:
    """Load the root-owned secret before Celery drops worker privileges."""
    token = settings.bale_bot_token_value
    if not token:
        raise RuntimeError("Bale bot token is not configured")
    settings.bale_bot_token = SecretStr(token)
    settings.bale_bot_token_file = None


def main() -> None:
    preload_bale_bot_token()
    # Text uploads reuse the ingestion publisher. Import the task graph while
    # this root entrypoint can still read the root-owned RabbitMQ secret; Celery
    # prefork children then inherit both initialized broker configurations.
    from app.integrations.bale import tasks as bale_tasks

    del bale_tasks
    setup_logging()
    celery_app.worker_main(
        argv=[
            "worker",
            "--loglevel=INFO",
            "--concurrency=1",
            f"--queues={settings.bale_queue_name}",
            "--hostname=bale@%h",
            "--uid=nobody",
            "--gid=nogroup",
        ]
    )


if __name__ == "__main__":
    main()
