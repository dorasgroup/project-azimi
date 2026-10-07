"""Pure in-memory source crawl and document preparation engine."""

from __future__ import annotations

import logging
from app.crawler.engine import CrawlEngine, crawl_execution_trace
from app.modules.monitoring.domain.crawl_policy import effective_crawl_policy
from app.modules.monitoring.domain.execution import SourceExecutionSpec
from app.processing.pipeline import ContentProcessor
from app.processing.schemas import ProcessingPolicy

logger = logging.getLogger("rasad.worker.source_execution")


class SourceExecutionEngine:
    """Executes crawl and content extraction in-memory without side effects."""

    async def execute_crawl(
        self,
        spec: SourceExecutionSpec,
        *, checkpoint=None,
    ) -> tuple[object, object, object]:
        """Crawl source and extract documents in memory.
        
        Returns (crawl_result, prepared_batch, crawl_policy).
        """
        crawl_policy = effective_crawl_policy(
            spec.crawl_config,
            date_from=spec.date_from,
            date_to=spec.date_to,
            source_count=spec.source_count,
        )
        processing_policy = ProcessingPolicy.model_validate(
            spec.crawl_config.get("processing_policy", {})
        )
        crawl_engine = CrawlEngine(crawl_policy, checkpoint=checkpoint)

        logger.info(
            "Executing crawl for source: %s (%s)",
            spec.title,
            spec.base_url,
            extra={"source_id": str(spec.source_id)},
        )

        crawl_result = await crawl_engine.crawl(
            spec.base_url,
            spec.topic,
            spec.date_from,
            spec.date_to,
        )

        content_processor = ContentProcessor(processing_policy)
        prepared_batch = content_processor.prepare_batch(
            crawl_result.pages,
            spec.topic,
            spec.title,
            spec.date_from,
            spec.date_to,
        )

        return crawl_result, prepared_batch, crawl_policy
