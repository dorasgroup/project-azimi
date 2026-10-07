"""Compatibility adapter for the established content-processing pipeline."""

from app.crawler.schemas import CrawledPage
from app.modules.documents.domain import (
    DocumentPreparationOutcome,
    DocumentPreparationRequest,
    RawDocumentPage,
    RejectedDocumentRecord,
)
from app.processing.pipeline import ContentProcessor
from app.processing.schemas import ProcessingPolicy, RejectedDocument

from .record_mapper import to_prepared_document_record


def _legacy_page(page: RawDocumentPage) -> CrawledPage:
    return CrawledPage.model_validate(
        {
            "url": page.url,
            "final_url": page.final_url,
            "success": page.success,
            "status_code": page.status_code,
            "title": page.title,
            "html": page.html,
            "markdown": page.markdown,
            "content_hash": page.content_hash,
            "cache_status": page.cache_status,
            "error": page.error,
            "metadata": dict(page.metadata),
        }
    )


def _rejected_record(document: RejectedDocument) -> RejectedDocumentRecord:
    return RejectedDocumentRecord(url=str(document.url), reason=document.reason)


class ContentProcessorAdapter:
    """Preserve processing semantics while owning both sides of the boundary."""

    def prepare(self, request: DocumentPreparationRequest) -> DocumentPreparationOutcome:
        policy = ProcessingPolicy.model_validate(dict(request.policy))
        result = ContentProcessor(policy).prepare_batch(
            [_legacy_page(page) for page in request.pages],
            request.topic,
            request.source_name,
            request.date_from,
            request.date_to,
        )
        return DocumentPreparationOutcome(
            documents=tuple(to_prepared_document_record(item) for item in result.documents),
            rejected=tuple(_rejected_record(item) for item in result.rejected),
            duplicate_count=result.duplicate_count,
        )
