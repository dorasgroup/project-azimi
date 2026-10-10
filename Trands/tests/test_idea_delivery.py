from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.monitoring.idea_client import IdeaClient, IdeaClientError
from apps.monitoring.idea_delivery_services import (
    build_idea_payload,
    maintain_idea_outbox,
    queue_idea_deliveries,
    send_idea_delivery,
    sync_idea_delivery,
)
from apps.monitoring.models import (
    AuditLog,
    ClusterItem,
    ContentItem,
    IdeaDelivery,
    Source,
    Topic,
    TrendCluster,
)


def make_cluster(status=TrendCluster.Status.TRENDING, text="خبر مهم فناوری"):
    topic = Topic.objects.create(name=f"موضوع ایده {Topic.objects.count() + 1}")
    channel_name = f"idea_source_{Source.objects.count() + 1}"
    source = Source.objects.create(
        platform=Source.Platform.TELEGRAM,
        identifier=channel_name,
        display_name="منبع آزمون ایده",
        public_url=f"https://t.me/s/{channel_name}",
    )
    item = ContentItem.objects.create(
        source=source,
        external_id=f"idea-item-{ContentItem.objects.count() + 1}",
        published_at=timezone.now() - timedelta(minutes=3),
        raw_text=text,
        normalized_text=text,
        public_url="https://example.com/idea-item",
    )
    cluster = TrendCluster.objects.create(
        topic=topic,
        status=status,
        score=84,
        first_published_at=item.published_at,
        last_published_at=item.published_at,
        representative=item,
        item_count=1,
        independent_source_count=1,
        platforms=["telegram"],
    )
    ClusterItem.objects.create(
        cluster=cluster,
        content_item=item,
        link_type=ClusterItem.LinkType.EXACT,
        is_representative=True,
    )
    return cluster


class FakeIdeaClient:
    def __init__(self, *, send_response=None, status_response=None, send_error=None):
        self.send_response = send_response or {}
        self.status_response = status_response or {}
        self.send_error = send_error
        self.send_calls = []
        self.status_calls = []

    def send_trend(self, payload, idempotency_key):
        self.send_calls.append((payload, idempotency_key))
        if self.send_error:
            raise self.send_error
        return self.send_response

    def get_trend_status(self, source_trend_id, source_version=1):
        self.status_calls.append((source_trend_id, source_version))
        return self.status_response


class FakeHTTPResponse:
    def __init__(self, status_code, data):
        self.status_code = status_code
        self.data = data

    def json(self):
        return self.data


class FakeHTTP:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        return self.response


def test_idea_client_uses_service_headers_and_safe_auth_error(settings):
    settings.IDEA_SOURCE_SYSTEM = "trend-monitor"
    http = FakeHTTP(FakeHTTPResponse(202, {"id": "receipt"}))
    client = IdeaClient(token="private-service-token", http_client=http)
    response = client.send_trend({"title": "نمونه"}, "trend-monitor:cluster:42:v1")
    assert response == {"id": "receipt"}
    _, path, kwargs = http.calls[0]
    assert path == "/integrations/trends"
    assert kwargs["headers"]["X-Integration-Token"] == "private-service-token"
    assert kwargs["headers"]["Idempotency-Key"] == "trend-monitor:cluster:42:v1"

    rejected = IdeaClient(
        token="private-service-token",
        http_client=FakeHTTP(FakeHTTPResponse(401, {})),
    )
    with pytest.raises(IdeaClientError) as exc:
        rejected.send_trend({"title": "نمونه"}, "trend-monitor:cluster:42:v1")
    assert exc.value.retryable is False
    assert "private-service-token" not in str(exc.value)


@pytest.mark.django_db
def test_outbox_is_idempotent_and_only_tracks_eligible_clusters(settings):
    settings.IDEA_SOURCE_SYSTEM = "trend-monitor"
    trending = make_cluster()
    confirmed = make_cluster(TrendCluster.Status.CONFIRMED)
    make_cluster(TrendCluster.Status.CANDIDATE)
    make_cluster(TrendCluster.Status.DISMISSED)

    first = queue_idea_deliveries()
    second = queue_idea_deliveries()

    assert len(first) == 2
    assert second == []
    assert IdeaDelivery.objects.count() == 2
    delivery = IdeaDelivery.objects.get(trend_cluster=trending)
    assert delivery.idempotency_key == f"trend-monitor:cluster:{trending.pk}:v1"
    assert delivery.payload == build_idea_payload(trending)
    assert IdeaDelivery.objects.filter(trend_cluster=confirmed).exists()


@pytest.mark.django_db
def test_disabled_maintenance_collects_outbox_without_external_enqueue(settings):
    settings.IDEA_INTEGRATION_ENABLED = False
    settings.IDEA_API_TOKEN = ""
    make_cluster()
    sent = []
    synced = []

    result = maintain_idea_outbox(
        send_enqueuer=sent.append,
        sync_enqueuer=synced.append,
    )

    assert result == {"created": 1, "send": 0, "sync": 0}
    assert sent == [] and synced == []
    assert IdeaDelivery.objects.get().idea_status == IdeaDelivery.IdeaStatus.PENDING


@pytest.mark.django_db
def test_successful_send_records_both_remote_stages_and_is_idempotent(settings):
    settings.IDEA_INTEGRATION_ENABLED = True
    settings.IDEA_API_TOKEN = "service-secret"
    delivery_id = queue_idea_deliveries(clusters=[make_cluster()])[0]
    client = FakeIdeaClient(
        send_response={
            "id": "idea-import-id",
            "upload_id": "idea-upload-id",
            "ingestion_job_id": "idea-job-id",
            "dify_status": "queued",
        }
    )

    sent = send_idea_delivery(delivery_id, client=client)
    sent_again = send_idea_delivery(delivery_id, client=client)

    assert sent.idea_status == IdeaDelivery.IdeaStatus.ACCEPTED
    assert sent.dify_status == IdeaDelivery.DifyStatus.QUEUED
    assert sent.remote_upload_id == "idea-upload-id"
    assert sent_again.pk == sent.pk
    assert len(client.send_calls) == 1
    assert "service-secret" not in repr(sent.payload)


@pytest.mark.django_db
def test_retryable_and_permanent_send_failures_are_bounded(settings):
    settings.IDEA_INTEGRATION_ENABLED = True
    settings.IDEA_API_TOKEN = "service-secret"
    retryable_id = queue_idea_deliveries(clusters=[make_cluster()])[0]
    retryable = send_idea_delivery(
        retryable_id,
        client=FakeIdeaClient(
            send_error=IdeaClientError("خطای امن", True, 503),
        ),
    )
    permanent_id = queue_idea_deliveries(clusters=[make_cluster()])[0]
    permanent = send_idea_delivery(
        permanent_id,
        client=FakeIdeaClient(
            send_error=IdeaClientError("درخواست رد شد", False, 401),
        ),
    )

    assert retryable.idea_status == IdeaDelivery.IdeaStatus.FAILED
    assert retryable.next_attempt_at is not None
    assert retryable.last_http_status == 503
    assert permanent.idea_status == IdeaDelivery.IdeaStatus.FAILED
    assert permanent.next_attempt_at is None
    assert "service-secret" not in retryable.error_summary + permanent.error_summary


@pytest.mark.django_db
def test_status_sync_marks_dify_complete_only_after_indexed(settings):
    settings.IDEA_INTEGRATION_ENABLED = True
    settings.IDEA_API_TOKEN = "service-secret"
    delivery_id = queue_idea_deliveries(clusters=[make_cluster()])[0]
    IdeaDelivery.objects.filter(pk=delivery_id).update(
        idea_status=IdeaDelivery.IdeaStatus.ACCEPTED,
        dify_status=IdeaDelivery.DifyStatus.INDEXING,
    )

    indexing = sync_idea_delivery(
        delivery_id,
        client=FakeIdeaClient(status_response={"dify_status": "indexing"}),
    )
    completed = sync_idea_delivery(
        delivery_id,
        client=FakeIdeaClient(
            status_response={
                "dify_status": "indexed",
                "dify_document_id": "dify-document-id",
            }
        ),
    )

    assert indexing.dify_completed_at is None
    assert completed.dify_status == IdeaDelivery.DifyStatus.INDEXED
    assert completed.dify_completed_at is not None
    assert completed.dify_document_id == "dify-document-id"


@pytest.mark.django_db
def test_cluster_deletion_preserves_delivery_snapshot():
    cluster = make_cluster()
    delivery_id = queue_idea_deliveries(clusters=[cluster])[0]
    title = IdeaDelivery.objects.get(pk=delivery_id).title
    cluster.delete()
    delivery = IdeaDelivery.objects.get(pk=delivery_id)
    assert delivery.trend_cluster is None
    assert delivery.title == title


@pytest.mark.django_db
def test_tracking_page_is_staff_only_and_retry_is_audited(client, settings):
    settings.IDEA_INTEGRATION_ENABLED = False
    delivery_id = queue_idea_deliveries(clusters=[make_cluster()])[0]
    IdeaDelivery.objects.filter(pk=delivery_id).update(
        idea_status=IdeaDelivery.IdeaStatus.FAILED,
        error_summary="خطای امن آزمون",
    )
    url = reverse("monitoring:idea_delivery_list")
    assert client.get(url).status_code == 302

    staff = get_user_model().objects.create_user(
        "idea-operator",
        password="password",
        is_staff=True,
    )
    client.force_login(staff)
    response = client.get(url)
    content = response.content.decode("utf-8")
    assert response.status_code == 200
    assert "ارسال به سامانه ایده‌پردازی" in content
    assert "خطای امن آزمون" in content
    assert reverse("monitoring:idea_delivery_retry", args=[delivery_id]) in content

    retry = client.post(reverse("monitoring:idea_delivery_retry", args=[delivery_id]))
    assert retry.status_code == 302
    assert IdeaDelivery.objects.get(pk=delivery_id).idea_status == IdeaDelivery.IdeaStatus.PENDING
    assert AuditLog.objects.filter(
        operation="idea_delivery_retry",
        entity_id=str(delivery_id),
    ).exists()
