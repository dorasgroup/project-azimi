from datetime import date
from uuid import uuid4

from app.crawler.schemas import CrawledPage
from app.schemas.monitoring import MonitoringJobCreate
from app.processing.dates import gregorian_to_jalali, jalali_to_gregorian
from app.processing.pipeline import ContentProcessor
from app.processing.schemas import ProcessingPolicy
from app.processing.text import detect_language


ARTICLE_HTML = """
<!doctype html>
<html lang="fa">
<head>
  <title>عنوان قدیمی سایت</title>
  <link rel="canonical" href="https://example.com/news/cognitive-war">
  <meta property="og:site_name" content="رسانه نمونه">
  <meta property="og:image" content="/images/lead.jpg">
  <script type="application/ld+json">
    {
      "@type": "NewsArticle",
      "headline": "تحلیل جنگ شناختی در رسانه‌ها",
      "datePublished": "2026-06-07T08:30:00+03:30",
      "author": {"@type": "Person", "name": "پوریا پژوهشگر"}
    }
  </script>
</head>
<body>
  <nav>خانه | درباره ما | تماس</nav>
  <article>
    <h2>تحلیل جنگ شناختی در رسانه‌ها</h2>
    <p>این گزارش به بررسی جنگ شناختی و شیوه روایت‌سازی رسانه‌ای در خبرهای روز می‌پردازد.</p>
    <p>در این تحلیل، چارچوب‌بندی پیام و تکرار هدفمند واژگان جنگ شناختی بررسی شده است.</p>
    <div class="advertisement">تبلیغ بزرگ و نامرتبط که نباید در متن نهایی باقی بماند.</div>
    <p>نتیجه پژوهش نشان می‌دهد که شناخت مخاطب و منبع خبر برای تحلیل دقیق ضروری است.</p>
  </article>
  <section class="comments">نظر کاربران که نباید در متن نهایی باقی بماند.</section>
  <footer>حق نشر و پیوندهای جانبی</footer>
</body>
</html>
"""


def page(url: str, html: str = ARTICLE_HTML) -> CrawledPage:
    return CrawledPage(
        url=url,
        final_url=url,
        success=True,
        status_code=200,
        html=html,
        markdown="# تحلیل جنگ شناختی",
    )


def verify() -> None:
    assert jalali_to_gregorian("1405/03/17") == date(2026, 6, 7)
    assert gregorian_to_jalali(date(2026, 6, 7)) == "1405/03/17"
    assert detect_language("این یک متن فارسی برای تشخیص زبان است") == "fa"
    assert detect_language("هذا نص عربي لاختبار اللغة العربية") == "ar"
    assert detect_language("This is an English text for language detection") == "en"
    request = MonitoringJobCreate(
        topic="جنگ شناختی",
        date_from_jalali="1405/03/17",
        date_to_jalali="1405/03/18",
        source_ids=[uuid4()],
    )
    assert request.date_from_gregorian == date(2026, 6, 7)
    assert request.date_to_gregorian == date(2026, 6, 8)

    processor = ContentProcessor(ProcessingPolicy(min_text_length=100, min_matched_score=0.1))
    result = processor.prepare_batch(
        [
            page("https://example.com/news/cognitive-war?utm_source=test"),
            page("https://example.com/news/cognitive-war-copy"),
            page(
                "https://example.com/news/short",
                "<html><body><article><p>جنگ شناختی کوتاه</p></article></body></html>",
            ),
        ],
        "جنگ شناختی",
        "منبع پیش‌فرض",
    )

    assert len(result.documents) == 1
    assert result.duplicate_count == 1
    assert len(result.rejected) == 1
    document = result.documents[0]
    assert document.title == "تحلیل جنگ شناختی در رسانه‌ها"
    assert document.author == "پوریا پژوهشگر"
    assert document.source_name == "رسانه نمونه"
    assert document.language == "fa"
    assert document.published_at_jalali == "1405/03/17"
    assert document.matched_score >= 0.5
    assert {"جنگ", "شناختی"}.issubset(set(document.matched_terms))
    assert str(document.canonical_url) == "https://example.com/news/cognitive-war"
    assert str(document.lead_image_url) == "https://example.com/images/lead.jpg"
    assert "تبلیغ" not in document.clean_text
    assert "نظر کاربران" not in document.clean_text
    assert "حق نشر" not in document.clean_text

    english_article = """
    <html lang="en">
      <head>
        <title>Iran and US resume nuclear talks in Oman</title>
        <meta property="article:published_time" content="2026-06-06T10:00:00+00:00">
      </head>
      <body>
        <article>
          <h1>Iran and US resume nuclear talks in Oman</h1>
          <p>Iranian and United States officials discussed the nuclear file and indirect negotiations.</p>
          <p>The talks focused on sanctions relief, diplomacy, and the future of the nuclear deal.</p>
        </article>
      </body>
    </html>
    """
    english_result = processor.prepare_batch(
        [page("https://example.com/world/iran-us-nuclear-talks", english_article)],
        "مذاکرات ایران و آمریکا",
        "English source",
        date(2026, 6, 1),
        date(2026, 6, 7),
    )
    assert len(english_result.documents) == 1
    assert english_result.documents[0].language == "en"
    assert {"iran", "us", "talks"} & set(english_result.documents[0].matched_terms)

    unrelated_english_article = (
        english_article.replace(
            "Iran and US resume nuclear talks in Oman",
            "Iran football team prepares for the World Cup",
        )
        .replace(
            "United States officials discussed the nuclear file and indirect negotiations.",
            "The national team held a training session before the tournament.",
        )
        .replace(
            "The talks focused on sanctions relief, diplomacy, and the future of the nuclear deal.",
            "The report focused on fans, coaches, and match preparation.",
        )
    )
    unrelated_result = processor.prepare_batch(
        [page("https://example.com/sport/iran-football", unrelated_english_article)],
        "مذاکرات ایران و آمریکا",
        "English source",
        date(2026, 6, 1),
        date(2026, 6, 7),
    )
    assert len(unrelated_result.documents) == 0
    assert (
        unrelated_result.rejected[0].reason
        == "Content relevance score is below the configured threshold"
    )

    strict_result = processor.prepare_batch(
        [
            page(
                "https://example.com/news/cognitive-war",
                ARTICLE_HTML.replace(
                    "2026-06-07T08:30:00+03:30",
                    "2026-06-07T23:10:05+00:00",
                ),
            )
        ],
        "جنگ شناختی",
        "منبع پیش‌فرض",
        date(2026, 6, 7),
        date(2026, 6, 7),
    )
    assert len(strict_result.documents) == 0
    assert strict_result.rejected[0].reason == "Publication date is after the requested date range"

    print(
        "Content processing verification passed: "
        f"{len(result.documents)} prepared, "
        f"{result.duplicate_count} duplicate, "
        f"{len(result.rejected)} rejected"
    )


if __name__ == "__main__":
    verify()
