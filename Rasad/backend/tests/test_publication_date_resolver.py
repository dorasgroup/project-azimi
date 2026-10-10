from datetime import UTC, date, datetime
from unittest import TestCase

import pytest

pytest.importorskip("bs4")
from bs4 import BeautifulSoup

from app.crawler.schemas import CrawledPage
from app.processing.dates import publication_local_date
from app.processing.extractor import extract_article
from app.processing.pipeline import ContentProcessor
from app.processing.publication_dates import PublicationDateResolver, parse_date_value
from app.processing.schemas import ProcessingPolicy


ARTICLE_BODY = " ".join(
    [
        "Women in Iran and media representation are examined in this detailed news report."
        for _ in range(8)
    ]
)


def _soup(head: str = "", body: str = ARTICLE_BODY) -> BeautifulSoup:
    return BeautifulSoup(f"<html><head>{head}</head><body><article>{body}</article></body></html>", "lxml")


class PublicationDateParsingTests(TestCase):
    def test_parses_persian_digits_jalali_numeric_date_in_tehran_timezone(self) -> None:
        parsed = parse_date_value("۱۴۰۴/۱۲/۰۹")
        self.assertIsNotNone(parsed)
        self.assertEqual(publication_local_date(parsed), date(2026, 2, 28))

    def test_parses_labelled_persian_month_date(self) -> None:
        parsed = parse_date_value("۹ اسفند ۱۴۰۴")
        self.assertIsNotNone(parsed)
        self.assertEqual(publication_local_date(parsed), date(2026, 2, 28))

    def test_jsonld_published_date_wins_over_modified_date(self) -> None:
        articles = [
            {
                "datePublished": "2026-03-10T10:30:00+03:30",
                "dateModified": "2026-04-01T09:00:00+03:30",
            }
        ]
        resolved = PublicationDateResolver().resolve(
            _soup(),
            "https://example.com/news/story",
            articles,
        )
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.source, "jsonld.datePublished")
        self.assertGreaterEqual(resolved.confidence, 0.99)
        self.assertEqual(resolved.published_at, datetime(2026, 3, 10, 7, 0, tzinfo=UTC))

    def test_meta_and_time_consensus_increases_confidence(self) -> None:
        soup = _soup(
            """
            <meta property="article:published_time" content="2026-03-10T10:30:00+03:30">
            <time itemprop="datePublished" datetime="2026-03-10T10:30:00+03:30"></time>
            """
        )
        resolved = PublicationDateResolver().resolve(
            soup,
            "https://example.com/news/story",
            [],
        )
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.source, "meta.article:published_time")
        self.assertEqual(resolved.confidence, 0.98)

    def test_url_and_labelled_visible_text_are_supported(self) -> None:
        from_url = PublicationDateResolver().resolve(
            _soup(),
            "https://example.com/news/1404/12/09/story",
            [],
        )
        from_text = PublicationDateResolver().resolve(
            _soup(body=f"تاریخ انتشار: ۹ اسفند ۱۴۰۴ {ARTICLE_BODY}"),
            "https://example.com/news/story",
            [],
        )
        self.assertEqual(from_url.source, "url.date")
        self.assertEqual(from_text.source, "visible_text.labelled_date")
        self.assertEqual(publication_local_date(from_text.published_at), date(2026, 2, 28))

    def test_rss_discovery_date_has_more_confidence_than_sitemap_lastmod(self) -> None:
        rss = PublicationDateResolver().resolve(
            _soup(),
            "https://example.com/news/story",
            [],
            {"discovered_via": "rss", "discovered_published_at": "2026-03-10T00:00:00Z"},
        )
        sitemap = PublicationDateResolver().resolve(
            _soup(),
            "https://example.com/news/story",
            [],
            {
                "discovered_via": "sitemap",
                "discovered_published_at": "2026-03-10T00:00:00Z",
            },
        )
        self.assertEqual(rss.confidence, 0.88)
        self.assertEqual(sitemap.confidence, 0.56)

    def test_extractor_returns_date_provenance(self) -> None:
        html = f"""
        <html><head>
          <script type="application/ld+json">
            {{"@type":"NewsArticle","headline":"Women in Iran", 
              "datePublished":"2026-03-10T10:30:00+03:30"}}
          </script>
        </head><body><article><p>{ARTICLE_BODY}</p></article></body></html>
        """
        extracted = extract_article(html, "https://example.com/news/story")
        self.assertEqual(extracted["published_at_source"], "jsonld.datePublished")
        self.assertEqual(extracted["published_at_confidence"], 0.99)
        self.assertEqual(extracted["publication_date_candidates"], 1)


class PublicationDatePipelineTests(TestCase):
    def _page(self, url: str, head: str) -> CrawledPage:
        return CrawledPage(
            url=url,
            success=True,
            html=f"<html><head>{head}</head><body><article><p>{ARTICLE_BODY}</p></article></body></html>",
        )

    def test_low_confidence_modified_date_is_rejected_for_date_range(self) -> None:
        page = self._page(
            "https://example.com/news/story",
            '<meta property="article:modified_time" content="2026-03-10T10:00:00Z">',
        )
        result = ContentProcessor(
            ProcessingPolicy(min_text_length=100, min_matched_score=0)
        ).prepare_batch(
            [page],
            "Women in Iran",
            date_from=date(2026, 3, 1),
            date_to=date(2026, 3, 31),
        )
        self.assertEqual(len(result.documents), 0)
        self.assertEqual(
            result.rejected[0].reason,
            "Publication date confidence is below the configured threshold",
        )

    def test_url_date_is_accepted_and_provenance_is_added_to_document(self) -> None:
        page = self._page("https://example.com/news/2026/03/10/women", "")
        document = ContentProcessor(
            ProcessingPolicy(min_text_length=100, min_matched_score=0)
        ).prepare_page(
            page,
            "Women in Iran",
            date_from=date(2026, 3, 1),
            date_to=date(2026, 3, 31),
        )
        self.assertEqual(document.published_at_source, "url.date")
        self.assertEqual(document.published_at_confidence, 0.72)
        self.assertEqual(document.published_at_jalali, "1404/12/19")
        self.assertEqual(document.metadata["publication_date_source"], "url.date")
