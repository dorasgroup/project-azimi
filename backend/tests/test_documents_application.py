from datetime import date
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from uuid import uuid4

from app.modules.documents.application import (
    GetSourceDocumentCounts,
    PersistPreparedDocuments,
    PrepareDocuments,
)
from app.modules.documents.domain import (
    DocumentPreparationOutcome,
    DocumentPreparationRequest,
    PreparedDocumentRecord,
)
from app.modules.documents.infrastructure.repository import SqlAlchemyDocumentRepository


def prepared_document(url: str = "https://example.com/a") -> PreparedDocumentRecord:
    return PreparedDocumentRecord(
        url=url,
        canonical_url=url,
        title="عنوان",
        clean_text="متن",
        markdown=None,
        author=None,
        source_name="منبع",
        published_at=None,
        published_at_jalali=None,
        language="fa",
        lead_image_url=None,
        matched_score=0.8,
        matched_terms=("موضوع",),
        content_hash="hash",
    )


class FakeProcessor:
    def __init__(self) -> None:
        self.request: DocumentPreparationRequest | None = None

    def prepare(self, request: DocumentPreparationRequest) -> DocumentPreparationOutcome:
        self.request = request
        return DocumentPreparationOutcome(
            documents=(prepared_document(),), rejected=(), duplicate_count=0
        )


class FakeRepository:
    def __init__(self) -> None:
        self.persisted: tuple | None = None
        self.counts: dict = {}

    async def replace_for_source(self, job_id, source_id, documents):
        self.persisted = (job_id, source_id, documents)
        return len(documents)

    async def source_counts(self, job_id):
        return self.counts.get(job_id, {})


class FakeSession:
    def __init__(self, existing: list[object | None]) -> None:
        self.existing = existing
        self.added: list[object] = []
        self.flushed = False

    async def scalar(self, statement):
        return self.existing.pop(0)

    def add(self, item) -> None:
        self.added.append(item)

    async def flush(self) -> None:
        self.flushed = True


class DocumentsPreparationTests(TestCase):
    def test_prepare_documents_preserves_processor_outcome(self) -> None:
        processor = FakeProcessor()
        request = DocumentPreparationRequest(
            pages=(),
            topic="موضوع",
            source_name="منبع",
            date_from=date(2026, 1, 1),
            date_to=None,
        )
        outcome = PrepareDocuments(processor).execute(request)
        self.assertIs(processor.request, request)
        self.assertEqual(outcome.documents, (prepared_document(),))


class DocumentsPersistenceTests(IsolatedAsyncioTestCase):
    async def test_persist_and_count_use_repository_contract(self) -> None:
        repository = FakeRepository()
        job_id, source_id = uuid4(), uuid4()
        documents = [prepared_document(), prepared_document("https://example.com/b")]
        self.assertEqual(
            await PersistPreparedDocuments(repository).execute(job_id, source_id, documents), 2
        )
        self.assertEqual(repository.persisted, (job_id, source_id, documents))
        repository.counts[job_id] = {source_id: 2}
        self.assertEqual(await GetSourceDocumentCounts(repository).execute(job_id), {source_id: 2})

    async def test_repository_skips_existing_url_and_persists_new_document(self) -> None:
        session = FakeSession([SimpleNamespace(id=1), None])
        repository = SqlAlchemyDocumentRepository(session)
        documents = [prepared_document(), prepared_document("https://example.com/b")]

        inserted = await repository.replace_for_source(uuid4(), uuid4(), documents)

        self.assertEqual(inserted, 1)
        self.assertEqual(len(session.added), 1)
        self.assertEqual(session.added[0].url, "https://example.com/b")
        self.assertTrue(session.flushed)
