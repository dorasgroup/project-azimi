import pytest
from django.conf import settings
from django.db import connection
from django.urls import reverse


def test_project_settings_load():
    assert settings.ROOT_URLCONF == "config.urls"
    assert "apps.core" in settings.INSTALLED_APPS


@pytest.mark.django_db
def test_home_page_opens(client):
    response = client.get(reverse("core:home"))
    assert response.status_code == 200
    assert "ورود به مرکز عملیات محتوا" in response.content.decode()
    assert 'dir="rtl"' in response.content.decode()


@pytest.mark.django_db
def test_health_endpoint_reports_healthy_services(client, settings):
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    response = client.get(reverse("core:health"))
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "services": {"web": "ok", "database": "ok", "redis": "ok"}}


@pytest.mark.django_db
def test_database_connection():
    connection.ensure_connection()
    assert connection.is_usable()
