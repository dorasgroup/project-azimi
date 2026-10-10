from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timezone

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.monitoring.collectors import TelegramCollector
from apps.monitoring.forms import SourceForm
from apps.monitoring.models import ContentItem, Source
from apps.monitoring.text_normalization import normalize_persian_text


FIXTURE = Path(__file__).parent / "fixtures" / "telegram_public_channel.html"


def parsed_result(html=None, cursor_id=""):
    collector = TelegramCollector()
    collector.fetch = lambda url: html if html is not None else FIXTURE.read_text(encoding="utf-8")
    return collector.collect(SimpleNamespace(identifier="samplechannel"), SimpleNamespace(last_external_id=cursor_id))


@pytest.mark.parametrize(
    ("url", "identifier"),
    [
        ("https://t.me/s/Sample_Channel", "sample_channel"),
        ("https://telegram.me/s/sample_channel", "sample_channel"),
    ],
)
def test_accepts_only_supported_public_telegram_channel_urls(url, identifier):
    assert Source.telegram_identifier_from_public_url(url) == identifier


@pytest.mark.parametrize(
    "url",
    [
        "http://t.me/s/sample_channel",
        "https://t.me/sample_channel",
        "https://t.me/+invitecode",
        "https://t.me/joinchat/invitecode",
        "https://t.me/s/sample_channel/extra",
        "https://t.me/s/abcd",
        "https://example.test/s/sample_channel",
    ],
)
def test_rejects_non_public_or_invalid_telegram_urls(url):
    with pytest.raises(ValidationError):
        Source.telegram_identifier_from_public_url(url)


@pytest.mark.django_db
def test_telegram_form_derives_identifier_from_url_and_ignores_manual_identifier():
    form = SourceForm(data={
        "platform": Source.Platform.TELEGRAM,
        "display_name": "نام دستی",
        "identifier": "different_channel",
        "public_url": "https://t.me/s/samplechannel",
        "weight": 1,
        "is_active": "on",
    })
    assert form.is_valid(), form.errors
    assert form.instance.identifier == "samplechannel"


def test_parser_extracts_channel_title_text_views_and_media_from_local_fixture():
    result = parsed_result()
    assert result.discovered_name == "اخبار نمونه"
    assert result.parse_error_count == 1
    assert [item.external_id for item in result.messages] == ["10", "11"]
    assert result.messages[0].raw_text == "متن يک پست با  كلمه\u200bهای فارسی"
    assert result.messages[0].metadata["views"] == "1.2K"
    assert result.messages[0].media_url == ""
    assert result.messages[1].content_type == "media"
    assert result.messages[1].media_url == "https://cdn.example.test/photo.jpg"


def test_parser_falls_back_cleanly_when_channel_title_is_missing():
    html = FIXTURE.read_text(encoding="utf-8").replace("<div class=\"tgme_channel_info_header_title\"><span dir=\"auto\">اخبار نمونه</span></div>", "").replace('<meta property="og:title" content="اخبار نمونه | Telegram">', "")
    assert parsed_result(html).discovered_name == ""


def test_invalid_media_url_is_not_returned():
    html = FIXTURE.read_text(encoding="utf-8").replace("https://cdn.example.test/photo.jpg", "javascript:alert(1)")
    media_item = parsed_result(html).messages[1]
    assert media_item.media_url == ""
    assert media_item.content_type == "message"


def test_parser_keeps_cursor_before_first_malformed_post():
    result = parsed_result()
    assert result.safe_cursor_external_id == "11"
    # The invalid post 12 remains eligible for a later retry.
    assert result.safe_cursor_external_id != "12"


def test_collector_starts_after_cursor_and_fetches_forward_pages():
    base = f"{settings.TELEGRAM_BASE_URL}/s/samplechannel"
    def page(*posts):
        return "".join(
            f'<div class="tgme_widget_message_wrap"><div data-post="samplechannel/{post_id}">'
            f'<time datetime="2026-09-22T07:38:42+00:00"></time>'
            f'<div class="tgme_widget_message_text">{text}</div></div></div>'
            for post_id, text in posts
        )

    pages = {
        f"{base}?after=100": page((101, "قدیمی"), (102, "ازدواج")),
        f"{base}?after=102": page((103, "جدید")),
        f"{base}?after=103": (
            '<meta property="og:title" content="اخبار نمونه | Telegram">'
            '<div class="tgme_widget_message_wrap"><div class="tme_no_messages_found">No posts found</div></div>'
        ),
    }
    collector = TelegramCollector()
    fetched = []
    collector.fetch = lambda url: (fetched.append(url), pages[url])[1]

    result = collector.collect(SimpleNamespace(identifier="samplechannel"), SimpleNamespace(last_external_id="100"))

    assert fetched == list(pages)
    assert [item.external_id for item in result.messages] == ["101", "102", "103"]
    assert result.messages[1].raw_text == "ازدواج"
    assert result.safe_cursor_external_id == "103"
    assert result.parse_error_count == 0


def test_collector_treats_repeated_forward_frontier_as_complete():
    base = f"{settings.TELEGRAM_BASE_URL}/s/samplechannel"
    post = (
        '<meta property="og:title" content="اخبار نمونه | Telegram">'
        '<div class="tgme_widget_message_wrap"><div data-post="samplechannel/101">'
        '<time datetime="2026-09-22T07:38:42+00:00"></time>'
        '<div class="tgme_widget_message_text">ازدواج</div></div></div>'
    )
    pages = {f"{base}?after=100": post, f"{base}?after=101": post}
    collector = TelegramCollector()
    collector.fetch = lambda url: pages[url]

    result = collector.collect(SimpleNamespace(identifier="samplechannel"), SimpleNamespace(last_external_id="100"))

    assert [item.external_id for item in result.messages] == ["101"]
    assert result.safe_cursor_external_id == "101"
    assert result.parse_error_count == 0


def test_collector_treats_empty_named_forward_page_as_complete():
    html = '<meta property="og:title" content="اخبار نمونه | Telegram"><title>Telegram: Contact</title>'

    result = parsed_result(html, cursor_id="100")

    assert result.messages == []
    assert result.parse_error_count == 0


def test_collector_does_not_advance_cursor_when_page_limit_interrupts_backfill():
    collector = TelegramCollector()
    collector.MAX_PAGES = 1
    collector.fetch = lambda url: (
        '<div class="tgme_widget_message_wrap"><div data-post="samplechannel/103">'
        '<time datetime="2026-09-22T07:38:42+00:00"></time></div></div>'
    )

    result = collector.collect(SimpleNamespace(identifier="samplechannel"), SimpleNamespace(last_external_id="100"))

    assert [item.external_id for item in result.messages] == ["103"]
    assert result.parse_error_count > 0
    assert result.safe_cursor_external_id == ""


def test_window_stops_at_end_without_advancing_past_future_post():
    collector = TelegramCollector()
    base = f"{settings.TELEGRAM_BASE_URL}/s/samplechannel"
    pages = {
        f"{base}?after=100": (
            '<div class="tgme_widget_message_wrap"><div data-post="samplechannel/101">'
            '<time datetime="2026-09-22T09:00:00+00:00"></time></div></div>'
            '<div class="tgme_widget_message_wrap"><div data-post="samplechannel/102">'
            '<time datetime="2026-09-22T11:00:00+00:00"></time></div></div>'
        ),
    }
    collector.fetch = lambda url: pages[url]

    result = collector.collect_window(
        SimpleNamespace(identifier="samplechannel"),
        SimpleNamespace(last_external_id="100"),
        datetime(2026, 9, 21, 10, tzinfo=timezone.utc),
        datetime(2026, 9, 22, 10, tzinfo=timezone.utc),
    )

    assert [item.external_id for item in result.messages] == ["101"]
    assert result.safe_cursor_external_id == "101"
    assert result.parse_error_count == 0


def test_window_collection_fetches_older_pages_until_start_date():
    collector = TelegramCollector()
    base = f"{settings.TELEGRAM_BASE_URL}/s/samplechannel"
    pages = {
        base: (
            '<div class="tgme_widget_message_wrap"><div data-post="samplechannel/103">'
            '<time datetime="2026-09-22T07:38:42+00:00"></time></div></div>'
        ),
        f"{base}?before=103": (
            '<div class="tgme_widget_message_wrap"><div data-post="samplechannel/102">'
            '<time datetime="2026-09-21T07:38:42+00:00"></time></div></div>'
        ),
    }
    fetched = []
    collector.fetch = lambda url: (fetched.append(url), pages[url])[1]

    result = collector.collect_window(
        SimpleNamespace(identifier="samplechannel"),
        SimpleNamespace(last_external_id=""),
        datetime(2026, 9, 22, tzinfo=timezone.utc),
        None,
    )

    assert fetched == list(pages)
    assert [item.external_id for item in result.messages] == ["102", "103"]
    assert result.parse_error_count == 0


def test_persian_text_normalization_is_conservative():
    raw = "  يک\u200b  كلمه\u200c با\t فاصله  "
    assert normalize_persian_text(raw) == "یک کلمه\u200c با فاصله"


@pytest.mark.django_db
def test_posts_searches_normalized_text_and_displays_raw_text(client):
    staff = get_user_model().objects.create_user("staff", password="password", is_staff=True)
    source = Source.objects.create(platform="telegram", public_url="https://t.me/s/samplechannel", display_name="")
    ContentItem.objects.create(
        source=source,
        external_id="1",
        published_at="2026-09-01T10:00:00+00:00",
        raw_text="متن يک",
        normalized_text="متن یک",
    )
    client.force_login(staff)
    response = client.get(reverse("monitoring:post_list"), {"q": "یک"})
    content = response.content.decode()
    assert response.status_code == 200
    assert "متن يک" in content
    assert "samplechannel" in content
