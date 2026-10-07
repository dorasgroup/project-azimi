from unittest import TestCase

import pytest

pytest.importorskip("bs4")

from app.modules.documents.domain import DocumentPreparationRequest, RawDocumentPage
from app.modules.documents.infrastructure import ContentProcessorAdapter


class ContentProcessorBoundaryTests(TestCase):
    def test_adapter_returns_domain_records_for_rejected_page(self) -> None:
        request = DocumentPreparationRequest(
            pages=(
                RawDocumentPage(
                    url="https://example.com/a",
                    final_url=None,
                    success=False,
                    error="crawl failed",
                ),
            ),
            topic="موضوع",
            source_name=None,
            date_from=None,
            date_to=None,
        )
        outcome = ContentProcessorAdapter().prepare(request)
        self.assertEqual(outcome.documents, ())
        self.assertEqual(outcome.rejected[0].url, "https://example.com/a")
        self.assertEqual(outcome.rejected[0].reason, "crawl failed")
