from datetime import timedelta
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.monitoring.models import AutomationRun, CollectionRun, CollectionRunSource, ContentItem, ClusterItem, Keyword, KeywordMatch, Source, SystemSetting, Topic, TrendCluster


@pytest.fixture
def staff(db):
    return get_user_model().objects.create_user("operator", password="Strong-pass-42", is_staff=True)


@pytest.mark.django_db
def test_root_is_dedicated_login_for_anonymous_user(client):
    response = client.get(reverse("core:home"))
    content = response.content.decode()
    assert response.status_code == 200
    assert "ورود به مرکز عملیات محتوا" in content
    assert 'autocomplete="current-password"' in content
    assert 'dir="rtl"' in content


@pytest.mark.django_db
def test_valid_staff_login_redirects_to_single_root_entry(client, staff):
    response = client.post(reverse("core:home"), {"username": "operator", "password": "Strong-pass-42"})
    assert response.status_code == 302
    assert response.url == reverse("core:home")
    assert client.get(response.url).status_code == 200


@pytest.mark.django_db
@override_settings(SHARED_ACCESS_TOKEN="test-shared-token")
def test_shared_link_signs_in_separate_operator_and_rejects_wrong_token(client):
    url = reverse("core:shared_access", args=["test-shared-token"])
    assert client.get(reverse("core:shared_access", args=["wrong-token"])).status_code == 404
    response = client.get(url)
    assert response.status_code == 302
    assert response.url == reverse("core:home")
    user = get_user_model().objects.get(username="shared_link_operator")
    assert user.is_staff and not user.is_superuser and not user.has_usable_password()
    assert client.get(reverse("core:home")).status_code == 200
    assert client.get(reverse("monitoring:topic_list")).status_code == 200
    assert client.get(reverse("core:profile")).status_code == 403


@pytest.mark.django_db
@override_settings(SHARED_ACCESS_TOKEN="")
def test_shared_link_is_disabled_without_token(client):
    assert client.get(reverse("core:shared_access", args=["anything"])).status_code == 404


@pytest.mark.django_db
def test_invalid_login_uses_generic_persian_message(client):
    response = client.post(reverse("core:home"), {"username": "missing", "password": "wrong"})
    assert response.status_code == 200
    assert "نام کاربری یا گذرواژه صحیح نیست" in response.content.decode()


@pytest.mark.django_db
def test_authenticated_staff_sees_dashboard_at_root(client, staff):
    client.force_login(staff)
    response = client.get(reverse("core:home"))
    assert response.status_code == 200
    assert '<h1 id="dashboard-title">داشبورد</h1>' in response.content.decode()


@pytest.mark.django_db
def test_authenticated_non_staff_gets_persian_403_at_root(client):
    member = get_user_model().objects.create_user("member", password="password")
    client.force_login(member)
    response = client.get(reverse("core:home"))
    assert response.status_code == 403
    assert "اجازه دسترسی ندارید" in response.content.decode()


@pytest.mark.django_db
def test_monitoring_dashboard_name_redirects_to_root(client, staff):
    client.force_login(staff)
    response = client.get(reverse("monitoring:dashboard"))
    assert response.status_code == 302
    assert response.url == reverse("core:home")


@pytest.mark.django_db
def test_logout_is_post_only_and_returns_to_login(client, staff):
    client.force_login(staff)
    assert client.get(reverse("core:logout")).status_code == 405
    response = client.post(reverse("core:logout"))
    assert response.status_code == 302
    assert response.url == reverse("core:home")


@pytest.mark.django_db
def test_operator_shell_has_csrf_logout_and_no_admin_link(client, staff):
    client.force_login(staff)
    content = client.get(reverse("core:home")).content.decode()
    assert reverse("core:logout") in content
    assert "csrfmiddlewaretoken" in content
    assert reverse("admin:index") not in content


@pytest.mark.django_db
def test_sidebar_uses_named_routes_and_marks_current_item(client, staff):
    client.force_login(staff)
    response = client.get(reverse("monitoring:topic_list"))
    content = response.content.decode()
    assert 'class="nav-item is-active" href="/monitoring/topics/"' in content
    assert reverse("monitoring:delivery_list") in content


@pytest.mark.django_db
def test_topics_page_renders_topics_and_links_to_topic_detail(client, staff):
    topic = Topic.objects.create(name="اقتصاد")
    Keyword.objects.create(topic=topic, phrase="نرخ ارز", match_type="contains")
    client.force_login(staff)

    response = client.get(reverse("monitoring:topic_list"), {"q": "نرخ"})
    content = response.content.decode()

    assert response.status_code == 200
    assert "موضوع‌ها و کلیدواژه‌ها" in content
    assert "اقتصاد" in content
    assert "نرخ ارز" in content
    assert reverse("monitoring:topic_create") in content
    assert reverse("monitoring:topic_detail", args=[topic.pk]) in content
    assert 'class="btn btn-primary topic-create-action"' in content
    for removed_text in (
        "پیکربندی رصد",
        "موضوع، حوزهٔ رصد است و با ورود به هر موضوع",
        "حوزه‌های رصد",
        "برای هر حوزه‌ای که می‌خواهید رصد شود",
    ):
        assert removed_text not in content

    # Entering the topic shows keywords and the form to add keywords inside the topic
    detail_response = client.get(reverse("monitoring:topic_detail", args=[topic.pk]))
    assert detail_response.status_code == 200
    detail_content = detail_response.content.decode()
    assert "اقتصاد" in detail_content
    assert "نرخ ارز" in detail_content
    assert 'name="match_type"' in detail_content
    assert 'name="phrase"' in detail_content


@pytest.mark.django_db
def test_topic_create_redirects_to_topic_detail_and_adds_keyword_inside(client, staff):
    client.force_login(staff)
    response = client.post(reverse("monitoring:topic_create"), {"name": "فناوری", "description": "", "is_active": "on"})
    topic = Topic.objects.get(name="فناوری")
    assert response.status_code == 302
    assert response.url == reverse("monitoring:topic_detail", args=[topic.pk])

    # Add keyword directly inside topic
    kw_resp = client.post(reverse("monitoring:topic_detail", args=[topic.pk]), {
        "phrase": "هوش مصنوعی",
        "match_type": "contains",
        "weight": 10,
        "is_active": "on",
    })
    assert kw_resp.status_code == 302
    assert Keyword.objects.filter(topic=topic, phrase="هوش مصنوعی").exists()


@pytest.mark.django_db
def test_keyword_list_is_staff_only_compatibility_redirect_to_combined_page(client, staff):
    keyword_url = reverse("monitoring:keyword_list")
    assert client.get(keyword_url).status_code == 302

    client.force_login(staff)
    response = client.get(keyword_url, {"q": "رصد"})

    assert response.status_code == 302
    assert response.url == f'{reverse("monitoring:topic_list")}?q=%D8%B1%D8%B5%D8%AF'


@pytest.mark.django_db
def test_combined_navigation_hides_standalone_keyword_link_and_covers_keyword_children(client, staff):
    topic = Topic.objects.create(name="سلامت")
    keyword = Keyword.objects.create(topic=topic, phrase="واکسن", match_type="exact")
    client.force_login(staff)

    content = client.get(reverse("monitoring:topic_list")).content.decode()
    assert "موضوع‌ها و کلیدواژه‌ها" in content
    assert 'href="/monitoring/keywords/"' not in content

    edit_content = client.get(reverse("monitoring:keyword_update", args=[keyword.pk])).content.decode()
    assert 'class="nav-item is-active" href="/monitoring/topics/"' in edit_content


@pytest.mark.django_db
def test_collection_run_is_not_in_everyday_navigation_but_technical_route_remains_available(client, staff):
    client.force_login(staff)
    dashboard_content = client.get(reverse("core:home")).content.decode()

    assert "اجراهای جمع‌آوری" not in dashboard_content
    assert client.get(reverse("monitoring:run_list")).status_code == 200


@pytest.mark.django_db
def test_source_group_is_not_in_everyday_navigation_but_technical_route_remains_available(client, staff):
    client.force_login(staff)
    dashboard_content = client.get(reverse("core:home")).content.decode()

    assert "گروه‌های منابع" not in dashboard_content
    assert client.get(reverse("monitoring:source_group_list")).status_code == 200


@pytest.mark.django_db
def test_source_list_and_form_have_no_group_field(client, staff):
    Source.objects.create(platform="telegram", display_name="تست منبع", identifier="testch", public_url="https://t.me/s/testch")
    client.force_login(staff)

    # Source list page
    response = client.get(reverse("monitoring:source_list"))
    assert response.status_code == 200
    content = response.content.decode()
    assert "تست منبع" in content
    assert "منابع عمومی" in content
    assert "منابع خاص" in content
    assert "همه گروه‌ها" not in content
    assert "<th>گروه</th>" not in content
    assert 'class="btn btn-primary source-create-action"' in content
    assert "پیکربندی رصد" not in content
    assert "کانال‌ها و حساب‌های عمومی پایش‌شده در پلتفرم‌های مختلف" not in content

    # Source create form
    create_response = client.get(reverse("monitoring:source_create"))
    assert create_response.status_code == 200
    create_content = create_response.content.decode()
    assert 'name="group"' not in create_content
    assert 'name="importance"' in create_content


@pytest.mark.django_db
def test_dashboard_shows_monitoring_counts_with_correct_status_semantics(client, staff):
    for status in (AutomationRun.Status.PENDING, AutomationRun.Status.RUNNING):
        AutomationRun.objects.create(trigger=AutomationRun.Trigger.MANUAL, status=status)
    for status in (
        AutomationRun.Status.SUCCESS,
        AutomationRun.Status.PARTIAL,
        AutomationRun.Status.FAILED,
        AutomationRun.Status.SKIPPED,
    ):
        AutomationRun.objects.create(trigger=AutomationRun.Trigger.MANUAL, status=status)
    client.force_login(staff)
    response = client.get(reverse("core:home"))
    content = response.content.decode()

    assert response.context["monitoring_in_progress_count"] == 2
    assert response.context["monitoring_completed_count"] == 4
    assert "رصدهای در حال انجام" not in content
    assert "رصدهای تکمیل‌شده" not in content
    assert "اجرای فعال در صف یا پردازش" not in content
    assert "مجموع اجراهای نهایی‌شده" not in content
    assert 'class="dash-kpis-grid"' not in content
    for removed_text in (
        "سامانه رصد / مرکز عملیات",
        "رصد، تحلیل و تحویل گزارش‌های مهم در یک نمای یکپارچه و قابل پیگیری.",
        "نرخ موفقیت",
        "هنوز زمان اجرای بعدی تعیین نشده است",
    ):
        assert removed_text not in content


@pytest.mark.django_db
def test_dashboard_mutating_actions_are_post_forms(client, staff):
    client.force_login(staff)
    content = client.get(reverse("core:home")).content.decode()
    assert f'action="{reverse("monitoring:automation_run_now")}"' not in content
    assert f'action="{reverse("monitoring:automation_configure")}"' in content
    assert "شروع رصد جدید" in content


@pytest.mark.django_db
def test_shell_loads_one_css_and_minimal_interaction_script(client, staff):
    client.force_login(staff)
    content = client.get(reverse("core:home")).content.decode()
    assert "/static/css/app.css" in content
    assert "/static/js/app.js" in content
    assert "data-sidebar-open" in content
    assert "cdn.jsdelivr.net" not in content
    assert "unpkg.com" not in content


def test_local_sahel_font_faces_are_available_to_the_shared_stylesheet():
    project_root = Path(__file__).resolve().parents[1]
    stylesheet = (project_root / "static" / "css" / "app.css").read_text(encoding="utf-8")

    assert 'font-family:"Sahel"' in stylesheet
    assert 'font-display:swap' in stylesheet
    assert '--font:"Sahel"' in stylesheet
    for weight, filename in (
        ("300", "Sahel-Light-FD-WOL"),
        ("400", "Sahel-FD-WOL"),
        ("600", "Sahel-SemiBold-FD-WOL"),
        ("700", "Sahel-Bold-FD-WOL"),
        ("900", "Sahel-Black-FD-WOL"),
    ):
        assert f"font-weight:{weight}" in stylesheet
        assert f"{filename}.woff2" in stylesheet
        assert (project_root / "static" / "fonts" / "sahel" / f"{filename}.woff2").is_file()


def test_operations_theme_has_local_mobile_navigation_contrast_rules():
    project_root = Path(__file__).resolve().parents[1]
    stylesheet = (project_root / "static" / "css" / "app.css").read_text(encoding="utf-8")

    assert ".menu-toggle span:not(.visually-hidden)" in stylesheet
    assert "background:currentColor!important" in stylesheet
    assert ".sidebar-close" in stylesheet
    assert "--ops-gold-bright" in stylesheet
    assert ".dash-panel{background:linear-gradient" in stylesheet
    assert "form.row,.filter-bar,.keyword-filter,.run-filter-form" in stylesheet
    assert ".configuration-section__intro{background:rgba(247,200,68,.06)" in stylesheet
    assert ".bale-kpi--success" in stylesheet


def test_bale_delivery_kpis_use_local_semantic_theme_classes():
    project_root = Path(__file__).resolve().parents[1]
    template = (project_root / "templates" / "monitoring" / "delivery_list.html").read_text(encoding="utf-8")

    for semantic_class in ("bale-kpi--neutral", "bale-kpi--success", "bale-kpi--danger", "bale-kpi--pending"):
        assert semantic_class in template
    for pale_color in ("#f1f5f9", "#ecfdf5", "#fef2f2", "#fffbeb"):
        assert pale_color not in template


@pytest.mark.django_db
def test_empty_dashboard_explains_next_operational_state(client, staff):
    client.force_login(staff)
    content = client.get(reverse("core:home")).content.decode()
    assert "هنوز ترندی شناسایی نشده است" in content
    assert "شروع رصد جدید" in content
    assert "رصد خودکار" in content
    assert "آخرین ترندها" in content
    assert "آخرین رصدها" not in content


@pytest.mark.django_db
def test_internal_lists_keep_filter_parameter_names_and_responsive_table(client, staff):
    client.force_login(staff)
    content = client.get(reverse("monitoring:post_list")).content.decode()
    for name in ("q", "platform", "source", "date_from", "date_to"):
        assert f'name="{name}"' in content
    assert "table" in content


@pytest.mark.django_db
def test_dashboard_monitoring_mode_actions_are_staff_only_and_use_post_forms(client, staff):
    recent_url = reverse("monitoring:run_recent_days")
    config_url = reverse("monitoring:automation_configure")
    assert client.post(recent_url).status_code == 302
    assert client.post(config_url).status_code == 302
    Source.objects.create(platform="telegram", public_url="https://t.me/s/uimodes", display_name="رسانه")
    client.force_login(staff)
    content = client.get(reverse("core:home")).content.decode()
    assert f'action="{recent_url}"' in content
    assert f'action="{config_url}"' in content
    assert 'name="days"' in content
    assert 'name="sources"' in content


@pytest.mark.django_db
def test_dashboard_monitoring_modes_expose_source_topic_modal_controls(client, staff):
    Source.objects.create(platform="telegram", public_url="https://t.me/s/uimodals", display_name="منبع مودال")
    Topic.objects.create(name="موضوع مودال")
    client.force_login(staff)
    content = client.get(reverse("core:home")).content.decode()
    for selector in ("recent-sources", "recent-topics", "automation-sources", "automation-topics"):
        assert f'data-selector-open="{selector}"' in content
        assert f'id="{selector}"' in content
    assert 'name="topics"' in content
    assert 'data-monitoring-switchboard' in content
    assert 'role="tablist"' in content
    assert 'id="monitoring-panel-automatic"' in content
    assert 'id="id_days"' in content
    assert 'id="id_automation_hours"' in content
    assert 'id="id_automation_interval_hours"' in content
    assert "رصد ۳ ساعت گذشته" in content
    assert "رصد ۶ ساعت گذشته" in content
    assert "رصد ۱۲ ساعت گذشته" in content
    assert "هر ۳ ساعت" in content
    assert "هر ۶ ساعت" in content
    assert "هر ۱۲ ساعت" in content
    assert "بازه محتوای رصد" in content
    assert "فاصله اجرای خودکار" in content
    assert "موضوع‌های تحلیل" in content


@pytest.mark.django_db
def test_selector_modals_and_settings_form_have_dark_theme_hooks(client, staff):
    Source.objects.create(platform="telegram", public_url="https://t.me/s/selector_theme", display_name="منبع انتخاب")
    Topic.objects.create(name="موضوع انتخاب")
    client.force_login(staff)

    dashboard = client.get(reverse("core:home")).content.decode()
    for selector in ("recent-sources", "recent-topics", "automation-sources", "automation-topics"):
        assert f'id="{selector}"' in dashboard
    assert 'class="source-options"' in dashboard

    settings_page = client.get(reverse("monitoring:settings"))
    settings_content = settings_page.content.decode()
    assert settings_page.status_code == 200
    assert "ویرایش تنظیمات سامانه" in settings_content
    assert "SystemSetting object" not in settings_content
    assert "form-panel--settings" in settings_content

    stylesheet = (Path(__file__).resolve().parents[1] / "static" / "css" / "app.css").read_text(encoding="utf-8")
    assert ".selector-modal__panel .source-options{background:#07171e!important" in stylesheet
    assert ".form-panel input[type=\"checkbox\"]" in stylesheet


@pytest.mark.django_db
def test_recent_days_form_validates_one_to_seven_days_and_selected_sources(client, staff, monkeypatch):
    source = Source.objects.create(platform="telegram", public_url="https://t.me/s/uiwindow", display_name="رسانه")
    topic = Topic.objects.create(name="موضوع بازه")
    client.force_login(staff)
    monkeypatch.setattr("apps.monitoring.views.execute_collection_run.delay", lambda *_: None)
    response = client.post(reverse("monitoring:run_recent_days"), {"days": "7", "sources": [source.pk], "topics": [topic.pk]})
    assert response.status_code == 302
    from apps.monitoring.models import CollectionRun
    run = CollectionRun.objects.latest("pk")
    assert run.run_type == CollectionRun.RunType.RECENT_DAYS
    assert run.source_results.get().source == source
    assert list(run.selected_topics.values_list("pk", flat=True)) == [topic.pk]


@pytest.mark.django_db
def test_automation_configuration_creates_schedule_without_default_selections(client, staff, monkeypatch):
    source = Source.objects.create(platform="telegram", public_url="https://t.me/s/uiautomationtopics", display_name="رسانه")
    topic = Topic.objects.create(name="موضوع خودکار فرم")
    client.force_login(staff)
    monkeypatch.setattr("apps.monitoring.views.execute_automation_pipeline.delay",lambda *_:None)
    dashboard=client.get(reverse("core:home")).content.decode()
    assert "انتخاب ذخیره‌شده" not in dashboard
    assert dashboard.count("انتخاب نشده")>=2
    response = client.post(
        reverse("monitoring:automation_configure"),
        {"hours":"3","interval_hours":"6","sources":[source.pk],"topics":[topic.pk]},
    )
    assert response.status_code == 302
    from apps.monitoring.models import AutomationSchedule
    schedule=AutomationSchedule.objects.get()
    assert schedule.interval_hours==6 and schedule.lookback_hours==3
    assert list(schedule.topics.values_list("pk",flat=True))==[topic.pk]
    run=AutomationRun.objects.get(schedule=schedule)
    assert list(run.selected_topics.values_list("pk", flat=True)) == [topic.pk]


@pytest.mark.django_db
def test_latest_runs_dashboard_partial_and_history_show_progress_actions_for_staff(client, staff):
    source = Source.objects.create(platform="telegram", public_url="https://t.me/s/liveprogress", display_name="منبع زنده")
    run = CollectionRun.objects.create(run_type=CollectionRun.RunType.MANUAL, status=CollectionRun.Status.RUNNING, target_sources_count=1)
    CollectionRunSource.objects.create(collection_run=run, source=source, status=CollectionRunSource.Status.RUNNING)
    client.force_login(staff)

    dashboard = client.get(reverse("core:home"))
    partial = client.get(reverse("monitoring:run_latest_partial"))
    history = client.get(reverse("monitoring:run_list"))
    for response in (dashboard, partial, history):
        assert response.status_code == 200
        assert "آخرین رصدها" in response.content.decode() or "تاریخچه رصدها" in response.content.decode()
    content = partial.content.decode()
    assert 'hx-trigger="every 8s"' in content
    assert "در حال بررسی منبع" in content
    assert reverse("monitoring:run_cancel", args=[run.pk]) in content


@pytest.mark.django_db
def test_collection_run_controls_are_staff_only_and_post_only(client, staff):
    run = CollectionRun.objects.create(run_type=CollectionRun.RunType.MANUAL, status=CollectionRun.Status.PENDING)
    cancel_url = reverse("monitoring:run_cancel", args=[run.pk])
    delete_url = reverse("monitoring:run_delete", args=[run.pk])
    assert client.get(cancel_url).status_code == 302
    assert client.post(cancel_url).status_code == 302
    run.refresh_from_db()
    assert run.status == CollectionRun.Status.PENDING
    client.force_login(staff)
    assert client.get(cancel_url).status_code == 405
    assert client.get(delete_url).status_code == 200
    assert client.post(delete_url).status_code == 302


@pytest.mark.django_db
def test_content_item_single_delete_by_staff(client, staff):
    source = Source.objects.create(platform="telegram", public_url="https://t.me/s/deletetest", display_name="تست حذف")
    item = ContentItem.objects.create(source=source, external_id="101", published_at=timezone.now(), raw_text="متن تست حذف")
    delete_url = reverse("monitoring:post_delete", args=[item.pk])

    # Unauthenticated rejected
    assert client.post(delete_url).status_code == 302
    assert ContentItem.objects.filter(pk=item.pk).exists()

    # Staff post deletes
    client.force_login(staff)
    response = client.post(delete_url, {"next": reverse("monitoring:post_list")}, follow=True)
    assert response.status_code == 200
    assert not ContentItem.objects.filter(pk=item.pk).exists()
    assert "محتوا با موفقیت حذف شد" in response.content.decode()


@pytest.mark.django_db
def test_content_item_bulk_delete_by_staff(client, staff):
    source = Source.objects.create(platform="telegram", public_url="https://t.me/s/bulktest", display_name="تست بالک")
    i1 = ContentItem.objects.create(source=source, external_id="201", published_at=timezone.now(), raw_text="محتوا ۱")
    i2 = ContentItem.objects.create(source=source, external_id="202", published_at=timezone.now(), raw_text="محتوا ۲")
    i3 = ContentItem.objects.create(source=source, external_id="203", published_at=timezone.now(), raw_text="محتوا ۳")
    bulk_url = reverse("monitoring:post_bulk_delete")

    # Unauthenticated rejected
    assert client.post(bulk_url, {"selected_ids": [i1.pk, i2.pk]}).status_code == 302
    assert ContentItem.objects.count() == 3

    # Staff deletes selected
    client.force_login(staff)
    response = client.post(bulk_url, {"selected_ids": [i1.pk, i2.pk]}, follow=True)
    assert response.status_code == 200
    assert not ContentItem.objects.filter(pk__in=[i1.pk, i2.pk]).exists()
    assert ContentItem.objects.filter(pk=i3.pk).exists()
    assert "2 محتوا با موفقیت حذف شد" in response.content.decode()


@pytest.mark.django_db
def test_content_item_purge_by_staff(client, staff):
    source = Source.objects.create(platform="telegram", public_url="https://t.me/s/purgetest", display_name="تست پاکسازی")
    for idx in range(5):
        ContentItem.objects.create(source=source, external_id=str(300 + idx), published_at=timezone.now(), raw_text=f"متن {idx}")
    purge_url = reverse("monitoring:post_purge")

    # Unauthenticated rejected
    assert client.post(purge_url).status_code == 302
    assert ContentItem.objects.count() == 5

    # Staff purges all
    client.force_login(staff)
    response = client.post(purge_url, follow=True)
    assert response.status_code == 200
    assert ContentItem.objects.count() == 0
    assert "پاکسازی شدند" in response.content.decode()


@pytest.mark.django_db
def test_post_list_renders_delete_controls_and_modal_for_staff(client, staff):
    source = Source.objects.create(platform="telegram", public_url="https://t.me/s/uiitem", display_name="منبع تست")
    item = ContentItem.objects.create(source=source, external_id="401", published_at=timezone.now(), raw_text="پست نمونه")
    client.force_login(staff)
    response = client.get(reverse("monitoring:post_list"))
    assert response.status_code == 200
    content = response.content.decode()
    assert "پاکسازی محتواها" in content
    assert "bulk-delete-form" in content
    assert reverse("monitoring:post_delete", args=[item.pk]) in content
    assert reverse("monitoring:post_bulk_delete") in content
    assert reverse("monitoring:post_purge") in content


@pytest.mark.django_db
def test_post_list_filters_by_topic_and_displays_keyword_badges(client, staff):
    source = Source.objects.create(platform="telegram", public_url="https://t.me/s/topicui", display_name="منبع خبری")
    topic = Topic.objects.create(name="سیاست")
    kw = Keyword.objects.create(topic=topic, phrase="آمریکا", match_type="contains")

    item1 = ContentItem.objects.create(source=source, external_id="501", published_at=timezone.now(), raw_text="پست درباره آمریکا و سیاست")
    item2 = ContentItem.objects.create(source=source, external_id="502", published_at=timezone.now(), raw_text="پست درباره آشپزی و سلامت")
    KeywordMatch.objects.create(content_item=item1, keyword=kw, topic=topic, match_type=kw.match_type, matched_phrase=kw.phrase)

    client.force_login(staff)
    # Check unfiltered: both items appear and badge appears for item1
    response = client.get(reverse("monitoring:post_list"))
    content = response.content.decode()
    assert "سیاست: آمریکا" in content
    assert 'name="topic"' in content

    # Check filtered by topic: only item1 appears
    response_filtered = client.get(reverse("monitoring:post_list"), {"topic": topic.pk})
    content_filtered = response_filtered.content.decode()
    assert "پست درباره آمریکا" in content_filtered
    assert "پست درباره آشپزی" not in content_filtered


@pytest.mark.django_db
def test_trend_cluster_list_renders_delete_controls_and_bulk_delete_for_staff(client, staff):
    topic = Topic.objects.create(name="جامعه")
    c1 = TrendCluster.objects.create(topic=topic, status=TrendCluster.Status.TRENDING, independent_source_count=2, score=70, first_published_at=timezone.now(), last_published_at=timezone.now())
    client.force_login(staff)
    response = client.get(reverse("monitoring:trend_cluster_list"))
    assert response.status_code == 200
    content = response.content.decode()
    assert "bulk-delete-form" in content
    assert reverse("monitoring:trend_cluster_delete", args=[c1.pk]) in content
    assert reverse("monitoring:trend_cluster_bulk_delete") in content
    assert "trend-checkbox" in content


@pytest.mark.django_db
def test_trend_cluster_single_delete(client, staff):
    topic = Topic.objects.create(name="جامعه")
    source = Source.objects.create(platform="telegram", public_url="https://t.me/s/trenddel", display_name="منبع تست")
    item = ContentItem.objects.create(source=source, external_id="601", published_at=timezone.now(), raw_text="پست نمونه ترند")
    cluster = TrendCluster.objects.create(topic=topic, status=TrendCluster.Status.TRENDING, independent_source_count=2, score=70, first_published_at=timezone.now(), last_published_at=timezone.now(), representative=item)
    ClusterItem.objects.create(cluster=cluster, content_item=item)

    client.force_login(staff)
    url = reverse("monitoring:trend_cluster_delete", args=[cluster.pk])
    resp = client.post(url)
    assert resp.status_code == 302
    assert not TrendCluster.objects.filter(pk=cluster.pk).exists()
    assert ContentItem.objects.filter(pk=item.pk).exists()


@pytest.mark.django_db
def test_trend_cluster_bulk_delete(client, staff):
    topic = Topic.objects.create(name="جامعه")
    c1 = TrendCluster.objects.create(topic=topic, status=TrendCluster.Status.TRENDING, independent_source_count=2, score=70, first_published_at=timezone.now(), last_published_at=timezone.now())
    c2 = TrendCluster.objects.create(topic=topic, status=TrendCluster.Status.TRENDING, independent_source_count=2, score=75, first_published_at=timezone.now(), last_published_at=timezone.now())
    c3 = TrendCluster.objects.create(topic=topic, status=TrendCluster.Status.TRENDING, independent_source_count=2, score=80, first_published_at=timezone.now(), last_published_at=timezone.now())

    client.force_login(staff)
    url = reverse("monitoring:trend_cluster_bulk_delete")
    resp = client.post(url, {"selected_ids": [c1.pk, c2.pk]})
    assert resp.status_code == 302
    assert not TrendCluster.objects.filter(pk=c1.pk).exists()
    assert not TrendCluster.objects.filter(pk=c2.pk).exists()
    assert TrendCluster.objects.filter(pk=c3.pk).exists()


@pytest.mark.django_db
def test_trend_cluster_list_excludes_single_source_and_no_show_all(client, staff):
    topic = Topic.objects.create(name="سیاست")
    multi_cluster = TrendCluster.objects.create(
        topic=topic,
        status=TrendCluster.Status.TRENDING,
        independent_source_count=3,
        score=85,
        first_published_at=timezone.now(),
        last_published_at=timezone.now(),
    )
    single_cluster = TrendCluster.objects.create(
        topic=topic,
        status=TrendCluster.Status.CANDIDATE,
        independent_source_count=1,
        score=40,
        first_published_at=timezone.now(),
        last_published_at=timezone.now(),
    )
    client.force_login(staff)
    response = client.get(reverse("monitoring:trend_cluster_list"))
    assert response.status_code == 200
    content = response.content.decode()
    assert "نمایش همه خوشه‌ها" not in content
    assert "show_all" not in content
    assert f"خوشه ترند #{multi_cluster.pk}" in content
    assert f"خوشه ترند #{single_cluster.pk}" not in content


@pytest.mark.django_db
def test_sidebar_does_not_contain_post_list_link(client, staff):
    client.force_login(staff)
    dashboard_content = client.get(reverse("core:home")).content.decode()
    assert "محتوای جمع‌آوری‌شده" not in dashboard_content
    assert 'href="/monitoring/posts/"' not in dashboard_content


@pytest.mark.django_db
def test_run_detail_displays_trends_posts_and_source_results_sections(client, staff):
    now = timezone.now()
    source1 = Source.objects.create(platform="telegram", public_url="https://t.me/s/runsrc1", display_name="منبع اول")
    source2 = Source.objects.create(platform="telegram", public_url="https://t.me/s/runsrc2", display_name="منبع دوم")
    topic = Topic.objects.create(name="اقتصاد")
    kw = Keyword.objects.create(topic=topic, phrase="دلار", match_type="contains")

    run = CollectionRun.objects.create(
        run_type=CollectionRun.RunType.RECENT_DAYS,
        status=CollectionRun.Status.SUCCESS,
        target_sources_count=2,
        received_content_count=2,
        new_content_count=2,
        requested_start_at=now - timedelta(days=7),
        requested_end_at=now,
    )
    run.selected_topics.add(topic)
    CollectionRunSource.objects.create(collection_run=run, source=source1, status=CollectionRunSource.Status.SUCCESS, received_count=1, new_count=1)
    CollectionRunSource.objects.create(collection_run=run, source=source2, status=CollectionRunSource.Status.SUCCESS, received_count=1, new_count=1)

    item1 = ContentItem.objects.create(
        source=source1, external_id="1001", published_at=now - timedelta(days=2), raw_text="خبر مهم نرخ دلار در بازار تهران"
    )
    item2 = ContentItem.objects.create(
        source=source2, external_id="1002", published_at=now - timedelta(days=2), raw_text="گزارش نوسان دلار امروز"
    )
    KeywordMatch.objects.create(content_item=item1, keyword=kw, topic=topic, match_type=kw.match_type, matched_phrase=kw.phrase)
    KeywordMatch.objects.create(content_item=item2, keyword=kw, topic=topic, match_type=kw.match_type, matched_phrase=kw.phrase)

    # Multi-source trend cluster for item1
    cluster = TrendCluster.objects.create(
        topic=topic, status=TrendCluster.Status.TRENDING, independent_source_count=2, score=85,
        first_published_at=now - timedelta(days=2), last_published_at=now - timedelta(days=2)
    )
    ClusterItem.objects.create(cluster=cluster, content_item=item1, similarity=0.9, link_type=ClusterItem.LinkType.EXACT)

    client.force_login(staff)
    detail_url = reverse("monitoring:run_detail", args=[run.pk])
    response = client.get(detail_url)
    assert response.status_code == 200
    content = response.content.decode()

    # Verify the 3 distinct sections
    assert "ترندهای شناسایی‌شده در این رصد" in content
    assert "حداقل ۳ منبع عمومی" in content
    assert "پست‌های جمع‌آوری‌شده در این رصد" in content
    assert "نتیجهٔ دریافت از منابع" in content

    # Verify content items and badges
    assert "منبع اول" in content
    assert "منبع دوم" in content
    assert "🔥 ترند عمومی (2 منبع)" in content
    assert "تک‌منبعی" in content
    assert reverse("monitoring:post_delete", args=[item1.pk]) in content
    assert reverse("monitoring:post_bulk_delete") in content

    # Verify trends_only filter inside run detail
    filtered_res = client.get(detail_url, {"trends_only": "1"})
    assert filtered_res.status_code == 200
    filtered_content = filtered_res.content.decode()
    assert "خبر مهم نرخ دلار" in filtered_content
    assert "گزارش نوسان دلار امروز" not in filtered_content


@pytest.mark.django_db
def test_sidebar_does_not_contain_trend_run_link(client, staff):
    client.force_login(staff)
    dashboard_content = client.get(reverse("core:home")).content.decode()
    assert "اجراهای تحلیل" not in dashboard_content
    assert 'href="/monitoring/trends/runs/"' not in dashboard_content


@pytest.mark.django_db
def test_trend_run_urls_redirect_to_trend_cluster_list(client, staff, monkeypatch):
    client.force_login(staff)
    response = client.get(reverse("monitoring:trend_run_list"))
    assert response.status_code == 302
    assert response.url == reverse("monitoring:trend_cluster_list")

    detail_response = client.get(reverse("monitoring:trend_run_detail", args=[1]))
    assert detail_response.status_code == 302
    assert detail_response.url == reverse("monitoring:trend_cluster_list")

    monkeypatch.setattr("apps.monitoring.views.execute_trend_analysis.delay", lambda *_: None)
    analyze_response = client.post(reverse("monitoring:trend_analyze_now"))
    assert analyze_response.status_code == 302
    assert analyze_response.url == reverse("monitoring:trend_cluster_list")
