import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db import transaction
from django.urls import reverse

from apps.monitoring.models import AuditLog, CollectionRun, Keyword, Source, SourceGroup, SystemSetting, Topic
from apps.monitoring.services import record_audit


@pytest.mark.django_db
def test_topic_and_keyword_uniqueness_and_validation():
    topic = Topic.objects.create(name="فناوری")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Topic.objects.create(name="فناوری")
    Keyword.objects.create(topic=topic, phrase="هوش مصنوعی", match_type=Keyword.MatchType.EXACT)
    duplicate = Keyword(topic=topic, phrase="هوش مصنوعی", match_type=Keyword.MatchType.EXACT)
    with pytest.raises(ValidationError):
        duplicate.full_clean()
    assert Keyword(topic=topic, phrase="هوش مصنوعی", match_type=Keyword.MatchType.HASHTAG).full_clean() is None


@pytest.mark.django_db
def test_source_normalizes_identifier_and_prevents_duplicate():
    source = Source.objects.create(platform=Source.Platform.TELEGRAM, display_name="کانال", public_url="https://t.me/s/Example_Channel")
    assert source.identifier == "example_channel"
    duplicate = Source(platform=Source.Platform.TELEGRAM, display_name="دوم", public_url="https://telegram.me/s/example_channel")
    with pytest.raises(ValidationError):
        duplicate.full_clean()


@pytest.mark.django_db
def test_system_setting_ranges_and_singleton():
    settings = SystemSetting.get_solo()
    assert settings.pk == SystemSetting.DEFAULT_PK
    settings.collection_interval_minutes = 1
    with pytest.raises(ValidationError):
        settings.full_clean()
    other = SystemSetting(pk=2)
    with pytest.raises(ValidationError):
        other.full_clean()


@pytest.mark.django_db
def test_safe_audit_log_creation_and_sensitive_metadata_rejected():
    topic = Topic.objects.create(name="اقتصاد")
    log = record_audit(actor=None, operation="create", instance=topic)
    assert log.entity_type == "monitoring.Topic"
    assert log.metadata == {"changed": True}
    unsafe = AuditLog(operation="create", entity_type="topic", entity_id="1", metadata={"token": "forbidden"})
    with pytest.raises(ValidationError):
        unsafe.full_clean()


@pytest.mark.django_db
def test_management_pages_require_staff(client):
    dashboard = reverse("monitoring:dashboard")
    assert client.get(dashboard).status_code == 302
    user = get_user_model().objects.create_user(username="member", password="password")
    client.force_login(user)
    assert client.get(dashboard).status_code == 403


@pytest.mark.django_db
def test_staff_can_crud_topic_keyword_and_source(client):
    staff = get_user_model().objects.create_user(username="manager", password="password", is_staff=True)
    client.force_login(staff)
    response = client.post(reverse("monitoring:topic_create"), {"name": "سلامت", "description": "", "is_active": "on"})
    assert response.status_code == 302
    topic = Topic.objects.get(name="سلامت")
    response = client.post(reverse("monitoring:keyword_create"), {"topic": topic.pk, "phrase": "واکسن", "match_type": "contains", "weight": 3, "is_active": "on"})
    assert response.status_code == 302
    response = client.post(reverse("monitoring:source_create"), {"platform": "telegram", "display_name": "خبر", "public_url": "https://t.me/s/news_channel", "importance": "general", "weight": 2, "is_active": "on"})
    assert response.status_code == 302
    assert Keyword.objects.filter(topic=topic, phrase="واکسن").exists()
    assert Source.objects.get(identifier="news_channel").is_active
    assert AuditLog.objects.filter(operation="create").count() == 3


@pytest.mark.django_db
def test_dashboard_handles_empty_and_populated_data(client):
    staff = get_user_model().objects.create_user(username="dashboard", password="password", is_staff=True)
    client.force_login(staff)
    assert client.get(reverse("monitoring:dashboard")).url == reverse("core:home")
    response = client.get(reverse("core:home"))
    assert "شروع رصد جدید" in response.content.decode()
    topic = Topic.objects.create(name="علم")
    Keyword.objects.create(topic=topic, phrase="فضا", match_type="exact")
    Source.objects.create(platform="telegram", display_name="کانال", public_url="https://t.me/s/space_channel")
    CollectionRun.objects.create(run_type="manual", status="success")
    response = client.get(reverse("core:home"))
    assert response.status_code == 200
    assert "رصدهای در حال انجام" in response.content.decode()
    assert "رصدهای تکمیل‌شده" in response.content.decode()
