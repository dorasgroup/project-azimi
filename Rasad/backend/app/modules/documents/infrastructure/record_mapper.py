"""Explicit mapping from legacy processing DTOs to Documents domain records."""

from app.modules.documents.domain import PreparedDocumentRecord
from app.processing.schemas import PreparedDocument


def to_prepared_document_record(document: PreparedDocument) -> PreparedDocumentRecord:
    return PreparedDocumentRecord(
        url=str(document.url),
        canonical_url=str(document.canonical_url),
        title=document.title,
        clean_text=document.clean_text,
        markdown=document.markdown,
        author=document.author,
        source_name=document.source_name,
        published_at=document.published_at,
        published_at_jalali=document.published_at_jalali,
        language=document.language,
        lead_image_url=str(document.lead_image_url) if document.lead_image_url else None,
        matched_score=document.matched_score,
        matched_terms=tuple(document.matched_terms),
        content_hash=document.content_hash,
        published_at_source=document.published_at_source,
        published_at_confidence=document.published_at_confidence,
        published_at_raw=document.published_at_raw,
        metadata=dict(document.metadata),
    )
