"""Re-run the previously failing historical topic through crawl and preparation."""

import asyncio
from collections import Counter
from datetime import date

from app.crawler.engine import CrawlEngine
from app.crawler.schemas import CrawlPolicy
from app.processing.pipeline import ContentProcessor
from app.processing.schemas import ProcessingPolicy


TOPIC = "رویکرد رسانه های داخلی و خارجی به مسئله زن در ایران"
DATE_FROM = date(2026, 2, 28)
DATE_TO = date(2026, 4, 9)
SOURCES = (
    ("ایرنا", "https://www.irna.ir/"),
    ("BBC فارسی", "https://www.bbc.com/persian"),
    ("رادیو فردا", "https://www.radiofarda.com/"),
)


async def verify() -> None:
    attempts: list[str] = []
    total_documents = 0
    for source_name, source_url in SOURCES:
        result = await CrawlEngine(
            CrawlPolicy(
                max_pages=20,
                max_concurrency=3,
                max_sitemaps=60,
                max_discovered_urls=2500,
                max_external_results=120,
                gdelt_discovery_enabled=False,
                common_crawl_discovery_enabled=True,
                candidate_provider_slots=5,
            )
        ).crawl(source_url, TOPIC, DATE_FROM, DATE_TO)
        prepared = ContentProcessor(
            ProcessingPolicy(min_text_length=100, min_matched_score=0.01)
        ).prepare_batch(
            result.pages,
            TOPIC,
            source_name,
            DATE_FROM,
            DATE_TO,
        )
        rejections = Counter(item.reason for item in prepared.rejected)
        page_failures = Counter(
            str(page.metadata.get("failure_kind") or page.error or "unknown")
            for page in result.pages
            if not page.success
        )
        attempts.append(
            f"{source_name}: discovered={result.diagnostics.discovered_total}, "
            f"selected={result.diagnostics.selected_total}, "
            f"fetched={result.diagnostics.successful_pages}, "
            f"documents={len(prepared.documents)}, "
            f"providers={result.diagnostics.selected_provider_counts}, "
            f"page_failures={dict(page_failures.most_common(3))}, "
            f"rejections={dict(rejections.most_common(4))}, "
            f"reason={result.diagnostics.reason_code}"
        )
        total_documents += len(prepared.documents)
        if total_documents:
            break

    assert total_documents > 0, "Historical acceptance failed: " + " | ".join(attempts)
    print("Historical monitoring acceptance passed: " + " | ".join(attempts))


if __name__ == "__main__":
    asyncio.run(verify())
