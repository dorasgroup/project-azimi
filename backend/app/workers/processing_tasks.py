"""Celery transport adapters for document-processing operations."""

from app.bootstrap.processing_worker import run_crawl_and_prepare_source, run_prepare_pages
from app.workers.celery_app import celery_app


@celery_app.task(name="processing.prepare_pages")
def prepare_pages(
    pages: list[dict],
    topic: str,
    source_name: str | None = None,
    policy: dict | None = None,
) -> dict:
    return run_prepare_pages(pages, topic, source_name, policy)


@celery_app.task(name="processing.crawl_and_prepare_source")
def crawl_and_prepare_source(
    source_url: str,
    topic: str,
    source_name: str | None = None,
    crawl_policy: dict | None = None,
    processing_policy: dict | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    return run_crawl_and_prepare_source(
        source_url,
        topic,
        source_name,
        crawl_policy,
        processing_policy,
        date_from,
        date_to,
    )
