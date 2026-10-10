from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.monitoring.automation_services import dispatch_scheduled_pipeline, execute_pipeline, release_lease, renew_lease, start_manual_pipeline, trend_clusters_for_automation
from apps.monitoring.models import AuditLog, AutomationRun, AutomationSchedule, AutomationState, BaleDestination, ClusterItem, CollectionRun, ContentItem, Delivery, Source, SystemSetting, Topic, TrendCluster
from apps.monitoring.retention_services import cleanup_retention


def make_source(name):
    return Source.objects.create(platform="telegram",public_url=f"https://t.me/s/{name}channel",display_name=name)


def make_schedule(name="scheduled",interval_hours=24,is_active=True):
    schedule=AutomationSchedule.objects.create(interval_hours=interval_hours,is_active=is_active)
    schedule.sources.add(make_source(name))
    schedule.topics.add(Topic.objects.create(name=f"موضوع {name}"))
    return schedule


def make_cluster(status=TrendCluster.Status.TRENDING):
    topic=Topic.objects.create(name=f"خودکار {Topic.objects.count()+1}")
    item=ContentItem.objects.create(source=make_source(f"auto{Source.objects.count()+1}"),external_id=str(ContentItem.objects.count()+1),published_at=timezone.now(),raw_text="خبر",normalized_text="خبر")
    cluster=TrendCluster.objects.create(topic=topic,status=status,first_published_at=item.published_at,last_published_at=item.published_at,representative=item)
    ClusterItem.objects.create(cluster=cluster,content_item=item,link_type="exact")
    return cluster,item


@pytest.mark.django_db
def test_disabled_or_not_due_automation_creates_no_run():
    assert dispatch_scheduled_pipeline() is None
    schedule=make_schedule(is_active=False)
    assert dispatch_scheduled_pipeline() is None
    schedule.is_active=True;schedule.last_started_at=timezone.now();schedule.save()
    assert dispatch_scheduled_pipeline() is None
    assert not AutomationRun.objects.exists()


@pytest.mark.django_db
def test_three_hour_schedule_becomes_due_after_three_hours():
    schedule=make_schedule("threehours",interval_hours=3)
    schedule.last_started_at=timezone.now()-timedelta(hours=2)
    schedule.save(update_fields=["last_started_at"])
    assert dispatch_scheduled_pipeline() is None
    schedule.last_started_at=timezone.now()-timedelta(hours=4)
    schedule.save(update_fields=["last_started_at"])
    assert dispatch_scheduled_pipeline().schedule_id==schedule.pk


@pytest.mark.django_db
def test_due_dispatcher_and_lock_allow_only_one_active_pipeline():
    make_schedule("first")
    run=dispatch_scheduled_pipeline()
    assert run.status==AutomationRun.Status.PENDING
    manual=start_manual_pipeline()
    assert manual.status==AutomationRun.Status.SKIPPED
    assert AutomationRun.objects.filter(status=AutomationRun.Status.PENDING).count()==1


@pytest.mark.django_db
def test_successful_lock_acquisition_has_random_owner_and_second_dispatcher_skips():
    make_schedule("lockone");make_schedule("locktwo")
    first=dispatch_scheduled_pipeline();state=AutomationState.get_solo()
    assert first.lease_owner and state.lock_owner==first.lease_owner and state.lock_expires_at>timezone.now()
    second=dispatch_scheduled_pipeline()
    assert second.status==AutomationRun.Status.SKIPPED


@pytest.mark.django_db
def test_expired_lease_is_recovered_by_new_owner():
    make_schedule("recovered")
    state=AutomationState.get_solo();state.lock_owner="old-owner";state.lock_expires_at=timezone.now()-timedelta(seconds=1);state.save()
    run=dispatch_scheduled_pipeline();state.refresh_from_db()
    assert run.status==AutomationRun.Status.PENDING and run.lease_owner!="old-owner" and state.lock_owner==run.lease_owner


@pytest.mark.django_db
def test_only_current_owner_can_renew_or_release_lease():
    run=start_manual_pipeline();state=AutomationState.get_solo();old_expiry=state.lock_expires_at
    assert renew_lease(run)
    state.lock_owner="new-owner";state.lock_expires_at=timezone.now()+timedelta(minutes=30);state.save()
    assert not renew_lease(run) and not release_lease(run)
    state.refresh_from_db();assert state.lock_owner=="new-owner" and state.lock_expires_at>old_expiry


@pytest.mark.django_db
def test_pipeline_stops_before_next_stage_when_lease_is_lost(monkeypatch):
    run=start_manual_pipeline();state=AutomationState.get_solo();state.lock_owner="replacement";state.save()
    calls=[];monkeypatch.setattr("apps.monitoring.automation_services._collection",lambda *_: calls.append("collection"))
    result=execute_pipeline(run.pk)
    assert result.status==AutomationRun.Status.FAILED and result.error_summary=="LeaseLostError" and not calls


@pytest.mark.django_db
def test_pipeline_orders_collections_analysis_and_idempotent_delivery(monkeypatch):
    destination=BaleDestination.objects.create(name="خودکار",chat_id="-10001")
    cluster,_=make_cluster(); calls=[]
    import apps.monitoring.automation_services as service
    telegram=CollectionRun.objects.create(run_type=CollectionRun.RunType.MANUAL,status=CollectionRun.Status.SUCCESS)
    x=CollectionRun.objects.create(run_type=CollectionRun.RunType.MANUAL,status=CollectionRun.Status.SUCCESS)
    def collected(platform,trigger):
        calls.append(platform)
        return telegram if platform==Source.Platform.TELEGRAM else x
    monkeypatch.setattr(service,"_collection",collected)
    original_analysis=service.create_analysis_run
    monkeypatch.setattr(service,"create_analysis_run",lambda: calls.append("analysis") or original_analysis())
    monkeypatch.setattr(service,"trend_clusters_for_automation",lambda _, topics=None: [cluster])
    monkeypatch.setattr(service,"create_delivery",lambda **_: (Delivery.objects.create(destination=destination,trend_cluster=cluster),True))
    run=start_manual_pipeline();result=execute_pipeline(run.pk,delivery_enqueuer=lambda _: calls.append("delivery"))
    assert calls==["telegram","x","analysis","delivery"]
    assert result.status==AutomationRun.Status.SUCCESS and result.deliveries_created==1


@pytest.mark.django_db
def test_pipeline_records_final_delivery_status_after_synchronous_send(monkeypatch):
    destination=BaleDestination.objects.create(name="آمار نهایی",chat_id="-10006")
    cluster,_=make_cluster()
    import apps.monitoring.automation_services as service
    telegram=CollectionRun.objects.create(run_type=CollectionRun.RunType.MANUAL,status=CollectionRun.Status.SUCCESS)
    x=CollectionRun.objects.create(run_type=CollectionRun.RunType.MANUAL,status=CollectionRun.Status.SUCCESS)
    monkeypatch.setattr(service,"_collection",lambda platform,*_: telegram if platform==Source.Platform.TELEGRAM else x)
    monkeypatch.setattr(service,"execute_analysis",lambda trend,topics=None: trend)
    monkeypatch.setattr(service,"sync_bale_destinations",lambda: None)
    monkeypatch.setattr(service,"trend_clusters_for_automation",lambda _,topics=None: [cluster])

    def mark_delivered(delivery_id):
        delivery=Delivery.objects.get(pk=delivery_id)
        delivery.status=Delivery.Status.DELIVERED
        delivery.save(update_fields=["status"])
        return delivery

    monkeypatch.setattr(service,"send_delivery",mark_delivered)
    run=start_manual_pipeline()
    result=execute_pipeline(run.pk)
    assert result.deliveries_created==1
    assert result.deliveries_delivered==1
    assert result.deliveries_failed==0


@pytest.mark.django_db
def test_partial_collection_still_runs_trend_analysis(monkeypatch):
    import apps.monitoring.automation_services as service
    telegram=CollectionRun.objects.create(run_type=CollectionRun.RunType.SCHEDULED,status=CollectionRun.Status.PARTIAL)
    x=CollectionRun.objects.create(run_type=CollectionRun.RunType.SCHEDULED,status=CollectionRun.Status.SUCCESS)
    monkeypatch.setattr(service,"_collection",lambda platform, _: telegram if platform=="telegram" else x)
    run=start_manual_pipeline();result=execute_pipeline(run.pk,delivery_enqueuer=lambda _: None)
    assert result.status==AutomationRun.Status.PARTIAL and result.trend_analysis_run_id is not None


@pytest.mark.django_db
def test_eligible_delivery_selection_excludes_all_existing_versions_and_human_statuses():
    destination=BaleDestination.objects.create(name="انتخاب",chat_id="-10002")
    trending,_=make_cluster();confirmed,_=make_cluster(TrendCluster.Status.CONFIRMED)
    for status in (TrendCluster.Status.SUPPRESSED,TrendCluster.Status.DISMISSED,TrendCluster.Status.RESOLVED): make_cluster(status)
    Delivery.objects.create(destination=destination,trend_cluster=trending,status=Delivery.Status.FAILED)
    assert list(trend_clusters_for_automation(destination))==[confirmed]


@pytest.mark.django_db
def test_automation_delivery_selection_is_limited_to_selected_topics():
    destination=BaleDestination.objects.create(name="انتخاب موضوع",chat_id="-10005")
    selected,_=make_cluster();other,_=make_cluster()
    assert list(trend_clusters_for_automation(destination,topics=[selected.topic]))==[selected]


@pytest.mark.django_db
def test_empty_saved_automation_sources_fall_back_to_all_active_sources(client):
    first=make_source("fallbackone");second=make_source("fallbacktwo")
    topic=Topic.objects.create(name="موضوع ذخیره")
    staff=get_user_model().objects.create_user("configstaff",password="password",is_staff=True)
    client.force_login(staff)
    response=client.post(reverse("monitoring:automation_configure"),{"interval_hours":"1","topics":[topic.pk]})
    assert response.status_code==302
    setting=SystemSetting.get_solo()
    assert not setting.automation_sources_configured
    run=start_manual_pipeline()
    assert set(run.selected_sources.values_list("pk",flat=True))=={first.pk,second.pk}


@pytest.mark.django_db
def test_legacy_configured_but_empty_sources_also_fall_back_to_all_active_sources():
    first=make_source("legacyone");second=make_source("legacytwo")
    setting=SystemSetting.get_solo()
    setting.automation_sources_configured=True
    setting.save(update_fields=["automation_sources_configured"])
    run=start_manual_pipeline()
    assert set(run.selected_sources.values_list("pk",flat=True))=={first.pk,second.pk}


@pytest.mark.django_db
def test_failed_or_recovered_pipeline_never_creates_duplicate_version_one_delivery():
    destination=BaleDestination.objects.create(name="بدون تکرار",chat_id="-10004")
    cluster,_=make_cluster();Delivery.objects.create(destination=destination,trend_cluster=cluster,status=Delivery.Status.FAILED)
    assert not trend_clusters_for_automation(destination).filter(pk=cluster.pk).exists()
    state=AutomationState.get_solo();state.lock_owner="crashed";state.lock_expires_at=timezone.now()-timedelta(seconds=1);state.save()
    make_schedule("recoverdelivery")
    recovered=dispatch_scheduled_pipeline()
    assert recovered.status==AutomationRun.Status.PENDING
    assert Delivery.objects.filter(destination=destination,trend_cluster=cluster,report_version=1).count()==1


@pytest.mark.django_db
def test_retention_dry_run_and_protected_content():
    setting=SystemSetting.get_solo();setting.retention_days=10;setting.save()
    old=ContentItem.objects.create(source=make_source("oldfree"),external_id="old",published_at=timezone.now()-timedelta(days=20),raw_text="قدیمی",normalized_text="قدیمی")
    protected_cluster,protected=make_cluster(TrendCluster.Status.CONFIRMED)
    protected.published_at=timezone.now()-timedelta(days=20);protected.save()
    destination=BaleDestination.objects.create(name="نگهداری",chat_id="-10003")
    delivered_cluster,delivered=make_cluster(TrendCluster.Status.RESOLVED)
    delivered.published_at=timezone.now()-timedelta(days=20);delivered.save();Delivery.objects.create(destination=destination,trend_cluster=delivered_cluster)
    assert cleanup_retention(dry_run=True)==1
    assert ContentItem.objects.filter(pk=old.pk).exists()
    assert cleanup_retention()==1
    assert not ContentItem.objects.filter(pk=old.pk).exists()
    assert ContentItem.objects.filter(pk__in=[protected.pk,delivered.pk]).count()==2


@pytest.mark.django_db
def test_retention_preserves_all_protected_cluster_statuses():
    setting=SystemSetting.get_solo();setting.retention_days=1;setting.save()
    items=[]
    for status in (TrendCluster.Status.CANDIDATE,TrendCluster.Status.TRENDING,TrendCluster.Status.CONFIRMED,TrendCluster.Status.SUPPRESSED):
        _,content=make_cluster(status);content.published_at=timezone.now()-timedelta(days=2);content.save();items.append(content.pk)
    assert cleanup_retention(dry_run=True)==0
    assert ContentItem.objects.filter(pk__in=items).count()==4


@pytest.mark.django_db
def test_automation_dashboard_actions_are_staff_only_and_audited(client,monkeypatch):
    run_url=reverse("monitoring:automation_run_now")
    assert client.post(run_url).status_code==302
    staff=get_user_model().objects.create_user("autostaff",password="password",is_staff=True);client.force_login(staff)
    monkeypatch.setattr("apps.monitoring.views.execute_automation_pipeline.delay",lambda *_: None)
    assert client.post(run_url).status_code==302
    assert client.post(reverse("monitoring:automation_toggle")).status_code==302
    assert client.get(reverse("monitoring:automation_run_list")).status_code==200
    assert AuditLog.objects.filter(operation="automation_pipeline_manual").exists()
    assert AuditLog.objects.filter(operation="automation_toggle").exists()


@pytest.mark.django_db
def test_schedule_can_be_toggled_independently_by_staff(client):
    schedule=make_schedule()
    staff=get_user_model().objects.create_user("schedule-toggle",password="password",is_staff=True)
    client.force_login(staff)
    url=reverse("monitoring:automation_schedule_toggle",kwargs={"pk":schedule.pk})
    assert client.post(url).status_code==302
    schedule.refresh_from_db()
    assert not schedule.is_active
    assert AuditLog.objects.filter(operation="automation_schedule_toggle",entity_id=str(schedule.pk)).exists()
    assert client.post(url).status_code==302
    schedule.refresh_from_db()
    assert schedule.is_active


@pytest.mark.django_db
def test_schedule_can_be_deleted_without_removing_run_history(client):
    schedule=make_schedule("scheduledelete")
    run=AutomationRun.objects.create(
        trigger=AutomationRun.Trigger.SCHEDULED,
        status=AutomationRun.Status.SUCCESS,
        schedule=schedule,
    )
    staff=get_user_model().objects.create_user("schedule-delete",password="password",is_staff=True)
    client.force_login(staff)

    url=reverse("monitoring:automation_schedule_delete",kwargs={"pk":schedule.pk})
    assert client.post(url).status_code==302
    assert not AutomationSchedule.objects.filter(pk=schedule.pk).exists()
    run.refresh_from_db()
    assert run.schedule_id is None
    assert AuditLog.objects.filter(operation="automation_schedule_delete",entity_id=str(schedule.pk)).exists()


@pytest.mark.django_db
def test_automation_snapshots_configured_sources_and_pipeline_scopes_collections(monkeypatch):
    selected = make_source("scopeselected")
    other = make_source("scopeother")
    setting = SystemSetting.get_solo()
    setting.automation_enabled = True
    setting.automation_sources_configured = True
    setting.save()
    setting.automation_sources.set([selected])
    run = start_manual_pipeline()
    assert list(run.selected_sources.values_list("pk", flat=True)) == [selected.pk]
    setting.automation_sources.set([other])
    captured = []
    import apps.monitoring.automation_services as service
    telegram = CollectionRun.objects.create(run_type="manual", status="success")
    x = CollectionRun.objects.create(run_type="manual", status="success")
    def collect(platform, trigger, sources=None):
        captured.append(list(sources.values_list("pk", flat=True)))
        return telegram if platform == "telegram" else x
    monkeypatch.setattr(service, "_collection", collect)
    execute_pipeline(run.pk, delivery_enqueuer=lambda _: None)
    assert captured == [[selected.pk], [selected.pk]]


@pytest.mark.django_db
def test_unconfigured_automation_keeps_legacy_all_active_source_default():
    first = make_source("defaultfirst")
    second = make_source("defaultsecond")
    run = start_manual_pipeline()
    assert set(run.selected_sources.values_list("pk", flat=True)) == {first.pk, second.pk}


@pytest.mark.django_db
def test_automation_snapshots_configured_topics_and_scopes_analysis(monkeypatch):
    selected = Topic.objects.create(name="موضوع خودکار منتخب")
    other = Topic.objects.create(name="موضوع خودکار دیگر")
    setting = SystemSetting.get_solo()
    setting.automation_topics_configured = True
    setting.save()
    setting.automation_topics.set([selected])
    run = start_manual_pipeline()
    assert list(run.selected_topics.values_list("pk", flat=True)) == [selected.pk]

    import apps.monitoring.automation_services as service
    telegram = CollectionRun.objects.create(run_type="manual", status="success")
    x = CollectionRun.objects.create(run_type="manual", status="success")
    monkeypatch.setattr(service, "_collection", lambda platform, trigger, sources=None: telegram if platform == "telegram" else x)
    received = []
    monkeypatch.setattr(service, "execute_analysis", lambda trend, *, topics=None: received.extend(topics.values_list("pk", flat=True)) or trend)
    execute_pipeline(run.pk, delivery_enqueuer=lambda _: None)
    assert received == [selected.pk]
    assert other.pk not in received


@pytest.mark.django_db
def test_unconfigured_automation_keeps_all_active_topics_for_analysis_snapshot():
    first = Topic.objects.create(name="موضوع سازگاری اول")
    second = Topic.objects.create(name="موضوع سازگاری دوم")
    run = start_manual_pipeline()
    assert set(run.selected_topics.values_list("pk", flat=True)) == {first.pk, second.pk}


@pytest.mark.django_db
def test_automation_run_single_delete(client):
    from django.contrib.auth import get_user_model
    from django.urls import reverse
    operator = get_user_model().objects.create_user("operator_auto", password="password", is_staff=True)
    client.force_login(operator)

    run = AutomationRun.objects.create(trigger=AutomationRun.Trigger.SCHEDULED, status=AutomationRun.Status.SUCCESS)
    resp = client.post(reverse("monitoring:automation_run_delete", args=[run.pk]))
    assert resp.status_code == 302
    assert not AutomationRun.objects.filter(pk=run.pk).exists()

    # Non-terminal run cannot be deleted
    running = AutomationRun.objects.create(trigger=AutomationRun.Trigger.SCHEDULED, status=AutomationRun.Status.RUNNING)
    client.post(reverse("monitoring:automation_run_delete", args=[running.pk]))
    assert AutomationRun.objects.filter(pk=running.pk).exists()


@pytest.mark.django_db
def test_automation_run_bulk_delete(client):
    from django.contrib.auth import get_user_model
    from django.urls import reverse
    operator = get_user_model().objects.create_user("operator_auto2", password="password", is_staff=True)
    client.force_login(operator)

    r1 = AutomationRun.objects.create(trigger=AutomationRun.Trigger.SCHEDULED, status=AutomationRun.Status.SUCCESS)
    r2 = AutomationRun.objects.create(trigger=AutomationRun.Trigger.MANUAL, status=AutomationRun.Status.FAILED)
    running = AutomationRun.objects.create(trigger=AutomationRun.Trigger.SCHEDULED, status=AutomationRun.Status.RUNNING)

    resp = client.post(reverse("monitoring:automation_run_bulk_delete"), {"selected_ids": [r1.pk, r2.pk, running.pk]})
    assert resp.status_code == 302
    assert not AutomationRun.objects.filter(pk__in=[r1.pk, r2.pk]).exists()
    assert AutomationRun.objects.filter(pk=running.pk).exists()


@pytest.mark.django_db
def test_automation_run_stop(client):
    from django.contrib.auth import get_user_model
    from django.urls import reverse
    operator = get_user_model().objects.create_user("operator_stop", password="password", is_staff=True)
    client.force_login(operator)

    running = AutomationRun.objects.create(trigger=AutomationRun.Trigger.SCHEDULED, status=AutomationRun.Status.RUNNING, lease_owner="test-owner")
    state = AutomationState.objects.create(pk=1, singleton=True, lock_owner="test-owner", lock_expires_at=timezone.now()+timedelta(minutes=10))
    resp = client.post(reverse("monitoring:automation_run_stop", args=[running.pk]))
    assert resp.status_code == 302
    running.refresh_from_db()
    assert running.status == AutomationRun.Status.FAILED
    assert running.error_summary == "توسط کاربر متوقف شد"
    assert running.finished_at is not None
    state.refresh_from_db()
    assert state.lock_owner == ""
    assert state.lock_expires_at is None
    assert AuditLog.objects.filter(operation="automation_run_stop").exists()

    resp2 = client.post(reverse("monitoring:automation_run_stop", args=[running.pk]))
    assert resp2.status_code == 302


@pytest.mark.django_db
def test_automation_schedule_create_starts_now_and_is_editable(client,monkeypatch):
    from django.contrib.auth import get_user_model
    operator = get_user_model().objects.create_user("operator_interval", password="password", is_staff=True)
    client.force_login(operator)

    source=make_source("interval")
    topic=Topic.objects.create(name="بازه خودکار")
    monkeypatch.setattr("apps.monitoring.views.execute_automation_pipeline.delay",lambda *_:None)
    response = client.post(
        reverse("monitoring:automation_configure"),
        {"hours":"3","interval_hours":"6","sources":[source.pk],"topics":[topic.pk]},
    )
    assert response.status_code == 302
    schedule=AutomationSchedule.objects.get()
    run=AutomationRun.objects.get(schedule=schedule)
    assert response.url==reverse("monitoring:automation_run_detail",args=[run.pk])
    assert schedule.interval_hours==6 and schedule.lookback_hours==3
    assert run.lookback_hours==3
    assert list(schedule.sources.all())==[source]
    assert list(run.selected_topics.all())==[topic]
    response = client.post(
        reverse("monitoring:automation_schedule_update",args=[schedule.pk]),
        {"lookback_hours":"96","interval_hours":"168","sources":[source.pk],"topics":[topic.pk],"is_active":"on"},
    )
    schedule.refresh_from_db()
    assert response.status_code==302 and schedule.interval_hours==168 and schedule.lookback_hours==96


@pytest.mark.django_db
def test_automation_collection_uses_selected_lookback_window(monkeypatch):
    from apps.monitoring.automation_services import _collection

    source=make_source("lookbackwindow")
    monkeypatch.setattr("apps.monitoring.automation_services.execute_run",lambda run:run)
    before=timezone.now()
    run=_collection(Source.Platform.TELEGRAM,AutomationRun.Trigger.SCHEDULED,Source.objects.filter(pk=source.pk),lookback_hours=3)
    after=timezone.now()

    assert before <= run.requested_end_at <= after
    assert run.requested_end_at-run.requested_start_at==timedelta(hours=3)


@pytest.mark.django_db
def test_automation_run_list_has_per_schedule_edit_and_no_global_settings(client):
    from django.contrib.auth import get_user_model
    operator = get_user_model().objects.create_user("operator_gear", password="password", is_staff=True)
    client.force_login(operator)

    schedule=make_schedule("listed",interval_hours=48)

    response = client.get(reverse("monitoring:automation_run_list"))
    assert response.status_code == 200
    content = response.content.decode()
    assert "btn-open-settings" not in content
    assert "automation-settings-modal" not in content
    assert reverse("monitoring:automation_schedule_update",args=[schedule.pk]) in content
    assert "هر 2 روز" in content
    assert "1 روز گذشته" in content
