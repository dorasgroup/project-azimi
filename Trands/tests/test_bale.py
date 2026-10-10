from datetime import timedelta

import httpx
import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from apps.monitoring.bale_client import BaleClient, BaleClientError
from apps.monitoring.delivery_services import create_delivery, create_resend_delivery, eligible_clusters, format_trend_report, send_delivery
from apps.monitoring.models import AuditLog, BaleDestination, ClusterItem, ContentItem, Delivery, Keyword, KeywordMatch, Source, Topic, TrendCluster


class FakeResponse:
    def __init__(self, status_code=200, data=None, headers=None):
        self.status_code=status_code;self._data=data;self.headers=headers or {}
    def json(self):
        if isinstance(self._data,Exception): raise self._data
        return self._data


class FakeHTTP:
    def __init__(self, responses): self.responses=list(responses);self.calls=[]
    def post(self, url, **kwargs): self.calls.append((url,kwargs));result=self.responses.pop(0);return result() if callable(result) else result
    def get(self, url, **kwargs): self.calls.append((url,kwargs));result=self.responses.pop(0);return result() if callable(result) else result


def source(name):
    return Source.objects.create(platform="telegram",public_url=f"https://t.me/s/{name}channel",display_name=name)


def cluster(status=TrendCluster.Status.TRENDING, text="خبر مهم <script> https://safe.example/news"):
    topic=Topic.objects.create(name=f"موضوع {Topic.objects.count()+1}")
    item=ContentItem.objects.create(source=source(f"source{Source.objects.count()+1}"),external_id=str(ContentItem.objects.count()+1),published_at=timezone.now()-timedelta(minutes=3),raw_text=text,normalized_text=text,public_url="https://safe.example/news")
    result=TrendCluster.objects.create(topic=topic,status=status,score=82,first_published_at=item.published_at,last_published_at=item.published_at,representative=item,item_count=1,independent_source_count=1,platforms=["telegram"])
    ClusterItem.objects.create(cluster=result,content_item=item,link_type="exact",is_representative=True)
    return result


@pytest.mark.django_db
def test_bale_destination_validation_and_delivery_uniqueness():
    destination=BaleDestination(name="گروه اصلی",chat_id="-100123")
    destination.full_clean();destination.save()
    invalid=BaleDestination(name="نامعتبر",chat_id="not-a-chat")
    with pytest.raises(ValidationError): invalid.full_clean()
    record=Delivery.objects.create(destination=destination,trend_cluster=cluster())
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Delivery.objects.create(destination=destination,trend_cluster=record.trend_cluster)
    destination.is_active=False;destination.save()
    assert not destination.is_active


@pytest.mark.django_db
def test_eligible_clusters_excludes_human_rejections_and_delivered():
    destination=BaleDestination.objects.create(name="فعال",chat_id="-1001")
    trending=cluster();confirmed=cluster(TrendCluster.Status.CONFIRMED)
    for status in (TrendCluster.Status.SUPPRESSED,TrendCluster.Status.DISMISSED,TrendCluster.Status.RESOLVED,TrendCluster.Status.CANDIDATE): cluster(status)
    Delivery.objects.create(destination=destination,trend_cluster=trending,status=Delivery.Status.DELIVERED)
    assert list(eligible_clusters(destination))==[confirmed]
    destination.is_active=False;destination.save()
    assert not eligible_clusters(destination).exists()


@pytest.mark.django_db
def test_report_formatter_is_persian_safe_complete_and_bounded(settings):
    settings.SITE_BASE_URL="https://monitor.example"
    record=cluster(text="متن\x00 طولانی <b> و مفید " + "الف"*5000)
    keyword=Keyword.objects.create(topic=record.topic,phrase="فوری",match_type="contains")
    KeywordMatch.objects.create(content_item=record.representative,keyword=keyword,topic=record.topic,match_type="contains",matched_phrase="فوری")
    report=format_trend_report(record)
    assert "🔴 ترند جدید" in report and "موضوع:" in report and "مشاهده جزئیات:" in report
    assert "<" not in report and "\x00" not in report and len(report)<=4000


@pytest.mark.django_db
def test_report_keywords_match_representative_text_and_cluster_topic():
    record=cluster(text="گزارش تازه درباره جوانان و خانواده")
    representative_keyword=Keyword.objects.create(topic=record.topic,phrase="جوان",match_type="contains")
    KeywordMatch.objects.create(content_item=record.representative,keyword=representative_keyword,topic=record.topic,match_type="contains",matched_phrase="جوان")
    other_item=ContentItem.objects.create(source=source("unrelated"),external_id="unrelated",published_at=timezone.now(),raw_text="نامزد انتخابات آمریکا در جشن",normalized_text="نامزد انتخابات آمریکا در جشن")
    ClusterItem.objects.create(cluster=record,content_item=other_item,link_type="shared_phrase")
    other_topic=Topic.objects.create(name="سیاست")
    america=Keyword.objects.create(topic=other_topic,phrase="آمریکا",match_type="contains")
    celebration=Keyword.objects.create(topic=record.topic,phrase="جشن",match_type="contains")
    KeywordMatch.objects.create(content_item=other_item,keyword=america,topic=other_topic,match_type="contains",matched_phrase="آمریکا")
    KeywordMatch.objects.create(content_item=other_item,keyword=celebration,topic=record.topic,match_type="contains",matched_phrase="جشن")

    report=format_trend_report(record)

    assert "کلیدواژه‌های منطبق:\nجوان" in report
    assert "آمریکا" not in report
    assert "جشن" not in report


@pytest.mark.django_db
def test_report_formatter_handles_missing_optional_data():
    record=cluster(text="")
    record.representative=None;record.platforms=[];record.save()
    report=format_trend_report(record)
    assert "پلتفرم‌ها: نامشخص" in report
    assert "خلاصه:" not in report


@pytest.mark.django_db
def test_successful_mock_send_records_provider_id_and_is_idempotent():
    destination=BaleDestination.objects.create(name="ارسال",chat_id="-1002")
    delivery,_=create_delivery(destination=destination,cluster=cluster())
    http=FakeHTTP([FakeResponse(data={"ok":True,"result":{"message_id":77}})])
    delivered=send_delivery(delivery.pk,client=BaleClient(token="test-token",http_client=http))
    assert delivered.status==Delivery.Status.DELIVERED and delivered.provider_message_id=="77" and len(http.calls)==1
    send_delivery(delivery.pk,client=BaleClient(token="test-token",http_client=http))
    assert len(http.calls)==1


def test_missing_token_and_auth_errors_are_safe():
    with pytest.raises(BaleClientError,match="توکن ربات بله تنظیم نشده"):
        BaleClient(token="",http_client=FakeHTTP([])).get_me()
    for status in (401,403):
        with pytest.raises(BaleClientError,match="دسترسی ربات بله پذیرفته نشد"):
            BaleClient(token="secret-value",http_client=FakeHTTP([FakeResponse(status,data={})])).get_me()


@pytest.mark.django_db
def test_rate_limit_timeout_server_error_and_invalid_response_fail_safely(monkeypatch):
    monkeypatch.setattr("apps.monitoring.bale_client.time.sleep",lambda _:None)
    destination=BaleDestination.objects.create(name="خطا",chat_id="-1003")
    for responses,expected in [
        ([FakeResponse(429,{"parameters":{"retry_after":3}}),FakeResponse(429,{"parameters":{"retry_after":3}})],"محدودیت"),
        ([lambda: (_ for _ in ()).throw(httpx.TimeoutException("timeout")),lambda: (_ for _ in ()).throw(httpx.TimeoutException("timeout"))],"ارتباط"),
        ([FakeResponse(500,{}),FakeResponse(503,{})],"سرویس"),
        ([FakeResponse(200,{"ok":False})],"پاسخ معتبر"),
    ]:
        delivery=Delivery.objects.create(destination=destination,trend_cluster=cluster())
        failed=send_delivery(delivery.pk,client=BaleClient(token="test",http_client=FakeHTTP(responses)))
        assert failed.status==Delivery.Status.FAILED and expected in failed.error_summary
    assert Delivery.objects.filter(destination=destination,retry_after_at__isnull=False).exists()


@pytest.mark.django_db
def test_resend_creates_new_version_only_by_explicit_service_call():
    destination=BaleDestination.objects.create(name="نسخه",chat_id="-1004")
    original=Delivery.objects.create(destination=destination,trend_cluster=cluster(),status=Delivery.Status.DELIVERED)
    resend=create_resend_delivery(destination=destination,cluster=original.trend_cluster)
    assert resend.report_version==2 and resend.status==Delivery.Status.PENDING


@pytest.mark.django_db
def test_staff_only_bale_actions_and_audit_log(client,monkeypatch):
    destination=BaleDestination.objects.create(name="پنل",chat_id="-1005")
    record=cluster()
    send_url=reverse("monitoring:trend_bale_send",args=[record.pk])
    assert client.post(send_url,{"destination":destination.pk}).status_code==302
    assert client.get(reverse("monitoring:bale_destination_list")).status_code==302
    assert client.post(reverse("monitoring:bale_test_message",args=[destination.pk])).status_code==302
    staff=get_user_model().objects.create_user("balestaff",password="password",is_staff=True)
    client.force_login(staff)
    monkeypatch.setattr("apps.monitoring.views.execute_bale_delivery.delay",lambda *_: None)
    monkeypatch.setattr("apps.monitoring.views.BaleClient.get_me",lambda _: {"ok":True})
    monkeypatch.setattr("apps.monitoring.views.BaleClient.send_message",lambda *args: None)
    assert client.post(reverse("monitoring:bale_connection_check",args=[destination.pk])).status_code==302
    assert client.post(reverse("monitoring:bale_test_message",args=[destination.pk])).status_code==302
    response=client.post(send_url,{"destination":destination.pk})
    assert response.status_code==302
    delivery=Delivery.objects.get(destination=destination,trend_cluster=record)
    assert AuditLog.objects.filter(operation="bale_delivery_create",entity_id=str(delivery.pk)).exists()
    delivery.status=Delivery.Status.FAILED;delivery.save()
    assert client.post(reverse("monitoring:delivery_retry",args=[delivery.pk])).status_code==302
    assert AuditLog.objects.filter(operation="bale_delivery_retry",entity_id=str(delivery.pk)).exists()
    delivery.status=Delivery.Status.DELIVERED;delivery.save()
    assert client.post(reverse("monitoring:delivery_resend",args=[delivery.pk])).status_code==302
    assert Delivery.objects.filter(destination=destination,trend_cluster=record,report_version=2).exists()
    assert AuditLog.objects.filter(operation="bale_delivery_resend").exists()


@pytest.mark.django_db
def test_delivery_list_view_and_sidebar_rendering(client, monkeypatch):
    staff = get_user_model().objects.create_user("staff_bale_ui", password="password", is_staff=True)
    client.force_login(staff)

    destination = BaleDestination.objects.create(name="کانال تست بله", chat_id="99887766")
    rec = cluster(text="خبر تست برای بله")
    d1 = Delivery.objects.create(
        destination=destination,
        trend_cluster=rec,
        status=Delivery.Status.DELIVERED,
        provider_message_id="5544",
        delivered_at=timezone.now(),
    )
    rec2 = cluster(text="خبر ناموفق برای بله")
    d2 = Delivery.objects.create(
        destination=destination,
        trend_cluster=rec2,
        status=Delivery.Status.FAILED,
        error_summary="خطای تستی ارسال",
        attempt_count=2,
    )

    response = client.get(reverse("monitoring:delivery_list"))
    assert response.status_code == 200
    content = response.content.decode("utf-8")

    # Check sidebar link
    assert "ارسال به بله" in content
    assert reverse("monitoring:delivery_list") in content

    # Check page content
    assert "تاریخچه و وضعیت ارسال ترندها به بله" in content
    assert "کانال تست بله" in content
    assert "ارسال موفق" in content
    assert "#5544" in content
    assert "ناموفق (خطا)" in content
    assert "خطای تستی ارسال" in content
    assert reverse("monitoring:delivery_detail", args=[d1.pk]) in content
    assert reverse("monitoring:delivery_retry", args=[d2.pk]) in content
    assert reverse("monitoring:delivery_resend", args=[d1.pk]) in content
    assert "تغییر توکن بازوی بله" in content


@pytest.mark.django_db
def test_staff_can_delete_terminal_bale_deliveries_individually_and_in_bulk(client):
    staff = get_user_model().objects.create_user("staff_delivery_delete", password="password", is_staff=True)
    client.force_login(staff)
    destination = BaleDestination.objects.create(name="حذف ارسال", chat_id="99887777")
    delivered = Delivery.objects.create(destination=destination, trend_cluster=cluster(text="ارسال تحویل‌شده"), status=Delivery.Status.DELIVERED)
    failed = Delivery.objects.create(destination=destination, trend_cluster=cluster(text="ارسال ناموفق"), status=Delivery.Status.FAILED)
    pending = Delivery.objects.create(destination=destination, trend_cluster=cluster(text="ارسال در انتظار"), status=Delivery.Status.PENDING)

    assert client.post(reverse("monitoring:delivery_delete", args=[delivered.pk])).status_code == 302
    assert not Delivery.objects.filter(pk=delivered.pk).exists()
    assert AuditLog.objects.filter(operation="bale_delivery_delete", entity_id=str(delivered.pk)).exists()

    response = client.post(reverse("monitoring:delivery_bulk_delete"), {"selected_ids": [failed.pk, pending.pk]})
    assert response.status_code == 302
    assert not Delivery.objects.filter(pk=failed.pk).exists()
    assert Delivery.objects.filter(pk=pending.pk).exists()


@pytest.mark.django_db
def test_bale_token_update_valid_and_invalid(client, monkeypatch, tmp_path):
    staff = get_user_model().objects.create_user("staff_token_test", password="password", is_staff=True)
    client.force_login(staff)

    # Mock env file
    fake_env = tmp_path / ".env"
    fake_env.write_text("BALE_BOT_TOKEN=old_token\nOTHER_KEY=val\n", encoding="utf-8")
    monkeypatch.setattr("django.conf.settings.BASE_DIR", tmp_path)

    # 1. Invalid token
    def mock_get_me_error(self):
        raise BaleClientError("دسترسی ربات بله پذیرفته نشد.")

    monkeypatch.setattr("apps.monitoring.views.BaleClient.get_me", mock_get_me_error)
    res_err = client.post(reverse("monitoring:bale_token_update"), {"token": "bad_token"})
    assert res_err.status_code == 302
    assert "old_token" in fake_env.read_text(encoding="utf-8")

    # 2. Valid token
    def mock_get_me_ok(self):
        return {
            "ok": True,
            "result": {
                "id": 123456,
                "is_bot": True,
                "first_name": "تست بات",
                "username": "test_token_bot",
            },
        }

    monkeypatch.setattr("apps.monitoring.views.BaleClient.get_me", mock_get_me_ok)
    res_ok = client.post(reverse("monitoring:bale_token_update"), {"token": "new_secret_token_12345"})
    assert res_ok.status_code == 302
    assert "BALE_BOT_TOKEN=new_secret_token_12345" in fake_env.read_text(encoding="utf-8")
    assert AuditLog.objects.filter(operation="bale_token_update").exists()


@pytest.mark.django_db
def test_bale_bot_check_view(client, monkeypatch):
    staff = get_user_model().objects.create_user("staff_check_test", password="password", is_staff=True)
    client.force_login(staff)

    monkeypatch.setattr(
        "apps.monitoring.views.BaleClient.get_me",
        lambda self: {
            "ok": True,
            "result": {
                "id": 987654,
                "first_name": "بات چک",
                "username": "check_bot",
            },
        },
    )
    res = client.post(reverse("monitoring:bale_bot_check"))
    assert res.status_code == 302
    session = client.session
    assert session.get("bale_bot_info", {}).get("username") == "check_bot"


@pytest.mark.django_db
def test_sync_bale_destinations_auto_discovers_chats(monkeypatch):
    from apps.monitoring.delivery_services import sync_bale_destinations

    mock_updates = {
        "ok": True,
        "result": [
            {
                "update_id": 1,
                "channel_post": {
                    "chat": {"id": 1001, "title": "کانال خبری", "type": "channel"}
                }
            },
            {
                "update_id": 2,
                "message": {
                    "chat": {"id": 2002, "title": "گروه مباحثه", "type": "group"}
                }
            },
        ]
    }

    class FakeClient:
        def get_updates(self):
            return mock_updates

    destinations = sync_bale_destinations(client=FakeClient())
    assert len(destinations) == 2
    channel_dest = BaleDestination.objects.get(chat_id="1001")
    group_dest = BaleDestination.objects.get(chat_id="2002")
    assert "کانال خبری" in channel_dest.name
    assert "گروه مباحثه" in group_dest.name
    assert channel_dest.is_active and group_dest.is_active


@pytest.mark.django_db
def test_dispatch_bale_deliveries_creates_and_enqueues():
    from apps.monitoring.delivery_services import dispatch_bale_deliveries

    dest = BaleDestination.objects.create(name="مقصد تست دستی", chat_id="999888", is_active=True)
    c = cluster(status=TrendCluster.Status.TRENDING)
    enqueued = []

    delivery_ids = dispatch_bale_deliveries(delivery_enqueuer=lambda pk: enqueued.append(pk))
    assert len(delivery_ids) == 1
    assert enqueued == delivery_ids
    assert Delivery.objects.filter(destination=dest, trend_cluster=c).exists()

    # Second run should not create duplicate deliveries
    second_run = dispatch_bale_deliveries(delivery_enqueuer=lambda pk: enqueued.append(pk))
    assert len(second_run) == 0


@pytest.mark.django_db
def test_dispatch_bale_deliveries_manual_run_with_force_resend():
    from datetime import timedelta
    from django.utils import timezone
    from apps.monitoring.delivery_services import dispatch_bale_deliveries

    dest = BaleDestination.objects.create(name="مقصد تست دستی ۲", chat_id="999889", is_active=True)
    c = cluster(status=TrendCluster.Status.TRENDING)
    # Simulate cluster was already delivered previously
    Delivery.objects.create(destination=dest, trend_cluster=c, report_version=1, status=Delivery.Status.DELIVERED, delivered_at=timezone.now() - timedelta(minutes=10))

    enqueued = []
    delivery_ids = dispatch_bale_deliveries(clusters=[c], force_resend=True, delivery_enqueuer=lambda pk: enqueued.append(pk))
    assert len(delivery_ids) == 1
    d2 = Delivery.objects.get(pk=delivery_ids[0])
    assert d2.report_version == 2
    assert d2.status == Delivery.Status.PENDING

