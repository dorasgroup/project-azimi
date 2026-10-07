import hashlib
from datetime import date

from app.crawler.schemas import CrawledPage
from app.processing.dates import gregorian_to_jalali, publication_local_date
from app.processing.dedupe import is_duplicate
from app.processing.extractor import extract_article
from app.processing.schemas import (
    PreparedDocument,
    ProcessingBatchResult,
    ProcessingPolicy,
    RejectedDocument,
)
from app.processing.scoring import relevance_score
from app.processing.text import detect_language


class ContentProcessor:
    def __init__(self, policy: ProcessingPolicy | None = None) -> None:
        self.policy = policy or ProcessingPolicy()

    def prepare_page(
        self,
        page: CrawledPage,
        topic: str,
        source_name: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> PreparedDocument:
        if not page.success or not page.html:
            raise ValueError(page.error or "Page crawl was not successful")
        extracted = extract_article(
            page.html,
            str(page.final_url or page.url),
            page.title,
            page.metadata,
        )
        clean_text = extracted["clean_text"]
        if len(clean_text) < self.policy.min_text_length:
            raise ValueError("Extracted content is too short")
        title = extracted["title"] or page.title or "بدون عنوان"
        score, matched_terms = relevance_score(topic, title, clean_text, extracted["canonical_url"])
        if score < self.policy.min_matched_score:
            raise ValueError("Content relevance score is below the configured threshold")
        content_hash = hashlib.sha256(clean_text.casefold().encode("utf-8")).hexdigest()
        published_at = extracted["published_at"]
        published_at_confidence = extracted["published_at_confidence"]
        if (date_from is not None or date_to is not None) and published_at is None:
            raise ValueError("Publication date is missing; requested date range cannot be verified")
        if (
            (date_from is not None or date_to is not None)
            and published_at_confidence is not None
            and published_at_confidence < self.policy.min_publication_date_confidence
        ):
            raise ValueError("Publication date confidence is below the configured threshold")
        if published_at is not None:
            published_date = publication_local_date(published_at)
            if date_from is not None and published_date < date_from:
                raise ValueError("Publication date is before the requested date range")
            if date_to is not None and published_date > date_to:
                raise ValueError("Publication date is after the requested date range")
        return PreparedDocument(
            url=page.final_url or page.url,
            canonical_url=extracted["canonical_url"],
            title=title,
            clean_text=clean_text,
            markdown=page.markdown,
            author=extracted["author"],
            source_name=extracted["source_name"] or source_name,
            published_at=published_at,
            published_at_jalali=(
                gregorian_to_jalali(publication_local_date(published_at))
                if published_at
                else None
            ),
            published_at_source=extracted["published_at_source"],
            published_at_confidence=published_at_confidence,
            published_at_raw=extracted["published_at_raw"],
            language=detect_language(clean_text),
            lead_image_url=extracted["lead_image_url"],
            matched_score=score,
            matched_terms=matched_terms,
            content_hash=content_hash,
            metadata={
                **{
                    str(key): str(value)
                    for key, value in page.metadata.items()
                    if value is not None
                },
                "publication_date_source": str(extracted["published_at_source"] or ""),
                "publication_date_confidence": str(published_at_confidence or ""),
                "publication_date_raw": str(extracted["published_at_raw"] or ""),
                "publication_date_candidates": str(extracted["publication_date_candidates"]),
            },
        )

    def prepare_batch(
        self,
        pages: list[CrawledPage],
        topic: str,
        source_name: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> ProcessingBatchResult:
        documents: list[PreparedDocument] = []
        rejected: list[RejectedDocument] = []
        duplicate_count = 0
        for page in pages:
            try:
                document = self.prepare_page(page, topic, source_name, date_from, date_to)
            except ValueError as exc:
                rejected.append(RejectedDocument(url=page.url, reason=str(exc)))
                continue
            if any(
                is_duplicate(
                    document,
                    existing,
                    title_threshold=self.policy.title_similarity_threshold,
                    content_threshold=self.policy.content_similarity_threshold,
                )
                for existing in documents
            ):
                duplicate_count += 1
                continue
            documents.append(document)
        return ProcessingBatchResult(
            documents=documents,
            rejected=rejected,
            duplicate_count=duplicate_count,
        )
