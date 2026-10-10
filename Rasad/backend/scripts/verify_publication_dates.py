"""Runtime verification for publication-date resolution inside the worker image."""

from datetime import date

from bs4 import BeautifulSoup

from app.processing.dates import publication_local_date
from app.processing.publication_dates import PublicationDateResolver, parse_date_value


def verify() -> None:
    jalali = parse_date_value("۱۴۰۴/۱۲/۰۹")
    named = parse_date_value("۹ اسفند ۱۴۰۴")
    assert jalali and publication_local_date(jalali) == date(2026, 2, 28)
    assert named and publication_local_date(named) == date(2026, 2, 28)

    soup = BeautifulSoup(
        """
        <html><head>
          <meta property="article:published_time" content="2026-03-10T10:30:00+03:30">
        </head><body><article>تاریخ انتشار: ۱۹ اسفند ۱۴۰۴</article></body></html>
        """,
        "lxml",
    )
    resolved = PublicationDateResolver().resolve(
        soup,
        "https://example.com/news/2026/03/10/story",
        [],
    )
    assert resolved is not None
    assert resolved.source == "meta.article:published_time"
    assert resolved.confidence >= 0.96
    assert len(resolved.candidates) >= 2
    print(
        "Publication date verification passed: "
        f"source={resolved.source}, confidence={resolved.confidence:.2f}, "
        f"candidates={len(resolved.candidates)}"
    )


if __name__ == "__main__":
    verify()
