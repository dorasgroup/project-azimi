"""Celery transport adapters for crawler operations."""

from app.bootstrap.crawler_worker import run_crawl_source, run_probe_source
from app.workers.celery_app import celery_app


@celery_app.task(name="crawler.probe_source")
def probe_source(url: str) -> dict:
    return run_probe_source(url)


@celery_app.task(name="crawler.crawl_source")
def crawl_source(
    source_url: str,
    topic: str,
    policy: dict | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    return run_crawl_source(source_url, topic, policy, date_from, date_to)
