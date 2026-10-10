from datetime import datetime, timezone

import httpx
import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse

from apps.monitoring.collection_services import create_manual_run, execute_run
from apps.monitoring.models import CollectionCursor, CollectionRun, ContentItem, Source
from apps.monitoring.x_collectors import XCollectorError, XRecentSearchCollector


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params, headers):
        self.calls.append({"url": url, "params": params, "headers": headers})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def x_source(handle="XDevelopers", save=False):
    identifier = Source.x_identifier_from_input(handle)
    source = Source(platform=Source.Platform.X, identifier=identifier, public_url=Source.x_public_url(identifier), display_name="")
    if save:
        source.save()
    return source


def payload(post_id="100", text="متن يک", next_token=None):
    meta = {"next_token": next_token} if next_token else {}
    return {
        "data": [{"id": post_id, "text": text, "created_at": "2026-09-01T10:00:00Z", "author_id": "42", "public_metrics": {"like_count": 3, "retweet_count": 2, "reply_count": 1, "quote_count": 4, "impression_count": 9}, "attachments": {"media_keys": ["m1"]}}],
        "includes": {"media": [{"media_key": "m1", "url": "https://media.example.test/photo.jpg"}]},
        "meta": meta,
    }


@pytest.mark.parametrize("value", ["XDevelopers", "@XDevelopers", "https://x.com/XDevelopers", "https://twitter.com/XDevelopers"])
def test_x_handle_and_url_are_canonicalized(value):
    assert Source.x_identifier_from_input(value) == "xdevelopers"


@pytest.mark.parametrize("value", ["https://x.com/user/status/1", "https://x.com/search?q=test", "https://x.com/i/flow", "http://x.com/user", "bad handle"])
def test_x_invalid_account_inputs_are_rejected(value):
    with pytest.raises(Exception):
        Source.x_identifier_from_input(value)


@override_settings(X_BEARER_TOKEN="test-token", X_MAX_POSTS_PER_SOURCE=100)
def test_x_collector_builds_query_sends_token_and_extracts_post():
    client = FakeClient([FakeResponse(payload=payload())])
    result = XRecentSearchCollector(client=client).collect(x_source(), type("Cursor", (), {"last_external_id": ""})())
    assert client.calls[0]["params"]["query"] == "from:xdevelopers -is:retweet -is:reply"
    assert client.calls[0]["params"]["start_time"].endswith("Z")
    assert client.calls[0]["headers"] == {"Authorization": "Bearer test-token"}
    post = result.messages[0]
    assert post.public_url == "https://x.com/xdevelopers/status/100"
    assert post.media_url == "https://media.example.test/photo.jpg"
    assert post.metadata["view_count"] == 9


@override_settings(X_BEARER_TOKEN="test-token", X_MAX_POSTS_PER_SOURCE=2)
def test_x_collector_uses_since_id_and_pagination():
    client = FakeClient([FakeResponse(payload=payload("100", next_token="next")), FakeResponse(payload=payload("101"))])
    result = XRecentSearchCollector(client=client).collect(x_source(), type("Cursor", (), {"last_external_id": "99"})())
    assert len(result.messages) == 2
    assert client.calls[0]["params"]["since_id"] == "99"
    assert client.calls[1]["params"]["next_token"] == "next"


@pytest.mark.django_db
@override_settings(X_BEARER_TOKEN="test-token")
def test_x_collection_updates_existing_post_without_duplicate():
    source = x_source(save=True)
    run = create_manual_run(Source.Platform.X)
    collector = XRecentSearchCollector(client=FakeClient([FakeResponse(payload=payload(text="نسخه يک"))]))
    execute_run(run, collector)
    second = create_manual_run(Source.Platform.X)
    collector = XRecentSearchCollector(client=FakeClient([FakeResponse(payload=payload(text="نسخه جدید"))]))
    execute_run(second, collector)
    assert ContentItem.objects.filter(source=source).count() == 1
    item = ContentItem.objects.get(source=source)
    assert item.raw_text == "نسخه جدید"
    assert item.normalized_text == "نسخه جدید"
    assert second.updated_content_count == 1


@pytest.mark.django_db
@override_settings(X_BEARER_TOKEN="test-token")
def test_x_cursor_does_not_advance_when_response_contains_invalid_post():
    source = x_source(save=True)
    invalid = {"data": [{"id": "broken", "created_at": "bad"}]}
    run = create_manual_run(Source.Platform.X)
    execute_run(run, XRecentSearchCollector(client=FakeClient([FakeResponse(payload=invalid)])))
    assert CollectionCursor.objects.get(source=source).last_external_id == ""
    assert run.status == "partial"


@override_settings(X_BEARER_TOKEN="test-token")
def test_x_rate_limit_and_auth_errors_are_safe():
    for response, expected in [(FakeResponse(429, headers={"x-rate-limit-reset": "123"}), "محدودیت درخواست"), (FakeResponse(401), "اعتبار یا سطح دسترسی")]:
        with pytest.raises(XCollectorError, match=expected):
            XRecentSearchCollector(client=FakeClient([response])).collect(x_source(), type("Cursor", (), {"last_external_id": ""})())


@override_settings(X_BEARER_TOKEN="test-token")
def test_x_network_error_has_limited_retries():
    client = FakeClient([httpx.ConnectError("down"), httpx.ConnectError("down"), httpx.ConnectError("down")])
    with pytest.raises(XCollectorError, match="موقتاً"):
        XRecentSearchCollector(client=client).collect(x_source(), type("Cursor", (), {"last_external_id": ""})())
    assert len(client.calls) == 3


@override_settings(X_BEARER_TOKEN="test-token")
def test_x_server_error_has_limited_retries():
    client = FakeClient([FakeResponse(503), FakeResponse(503), FakeResponse(503)])
    with pytest.raises(XCollectorError, match="موقتاً"):
        XRecentSearchCollector(client=client).collect(x_source(), type("Cursor", (), {"last_external_id": ""})())
    assert len(client.calls) == 3


@override_settings(X_BEARER_TOKEN="")
def test_x_without_token_fails_without_network_request():
    client = FakeClient([])
    with pytest.raises(XCollectorError, match="توکن API X"):
        XRecentSearchCollector(client=client).collect(x_source(), type("Cursor", (), {"last_external_id": ""})())
    assert client.calls == []


@pytest.mark.django_db
def test_x_manual_run_is_staff_only_and_targets_only_x_sources(client, monkeypatch):
    x_source(save=True)
    Source.objects.create(platform="telegram", public_url="https://t.me/s/channelname", display_name="")
    url = reverse("monitoring:run_x_now")
    assert client.post(url).status_code == 302
    staff = get_user_model().objects.create_user("xstaff", password="password", is_staff=True)
    client.force_login(staff)
    monkeypatch.setattr("apps.monitoring.views.execute_collection_run.delay", lambda run_id: None)
    response = client.post(url)
    assert response.status_code == 302
    run = CollectionRun.objects.latest("id")
    assert run.target_sources_count == 1
    assert run.source_results.get().source.platform == Source.Platform.X


@pytest.mark.django_db
def test_posts_can_filter_x_platform(client):
    staff = get_user_model().objects.create_user("filterstaff", password="password", is_staff=True)
    source = x_source(save=True)
    ContentItem.objects.create(source=source, external_id="1", published_at=datetime.now(timezone.utc), raw_text="X post", normalized_text="X post")
    client.force_login(staff)
    response = client.get(reverse("monitoring:post_list"), {"platform": "x"})
    assert response.status_code == 200
    assert "X post" in response.content.decode()
