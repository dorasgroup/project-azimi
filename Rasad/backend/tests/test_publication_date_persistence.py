from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.models import CrawledDocument
from app.modules.documents.domain import PreparedDocumentRecord
from app.modules.documents.infrastructure.record_mapper import to_prepared_document_record
from app.modules.documents.infrastructure.repository import SqlAlchemyDocumentRepository
from app.processing.schemas import PreparedDocument


def test_prepared_document_provenance_crosses_documents_boundary() -> None:
    document = PreparedDocument(
        url="https://example.com/news/1",
        canonical_url="https://example.com/news/1",
        title="News",
        clean_text="A sufficiently descriptive article body.",
        published_at=datetime(2026, 3, 10, tzinfo=UTC),
        published_at_jalali="1404/12/19",
        published_at_source="jsonld.datePublished",
        published_at_confidence=0.99,
        published_at_raw="2026-03-10T00:00:00Z",
        language="en",
        matched_score=1,
        content_hash="a" * 64,
    )

    record = to_prepared_document_record(document)

    assert record.published_at_source == "jsonld.datePublished"
    assert record.published_at_confidence == 0.99
    assert record.published_at_raw == "2026-03-10T00:00:00Z"


def test_crawled_document_model_contains_date_provenance_columns_and_constraint() -> None:
    table = CrawledDocument.__table__
    assert {
        "published_at_source",
        "published_at_confidence",
        "published_at_raw",
    }.issubset(table.columns.keys())
    assert any(
        (constraint.name or "").endswith("published_at_confidence_range")
        for constraint in table.constraints
    )


@pytest.mark.asyncio
async def test_repository_writes_date_provenance_to_orm_record() -> None:
    class FakeSession:
        def __init__(self) -> None:
            self.added: list[CrawledDocument] = []

        async def scalar(self, _statement):
            return None

        def add(self, value: CrawledDocument) -> None:
            self.added.append(value)

        async def flush(self) -> None:
            return None

    session = FakeSession()
    record = PreparedDocumentRecord(
        url="https://example.com/news/1",
        canonical_url="https://example.com/news/1",
        title="News",
        clean_text="Article body",
        markdown=None,
        author=None,
        source_name="Source",
        published_at=datetime(2026, 3, 10, tzinfo=UTC),
        published_at_jalali="1404/12/19",
        language="en",
        lead_image_url=None,
        matched_score=1,
        matched_terms=("news",),
        content_hash="a" * 64,
        published_at_source="jsonld.datePublished",
        published_at_confidence=0.99,
        published_at_raw="2026-03-10T00:00:00Z",
    )

    inserted = await SqlAlchemyDocumentRepository(session).replace_for_source(
        uuid4(),
        uuid4(),
        [record],
    )

    assert inserted == 1
    stored = session.added[0]
    assert stored.published_at_source == "jsonld.datePublished"
    assert stored.published_at_confidence == 0.99
    assert stored.published_at_raw == "2026-03-10T00:00:00Z"
