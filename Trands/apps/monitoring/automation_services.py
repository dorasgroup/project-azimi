"""Shared manual/scheduled monitoring pipeline; no collection logic is duplicated."""
from datetime import timedelta
import secrets

from django.db import transaction
from django.utils import timezone

from .collection_services import create_manual_run, execute_run
from .delivery_services import create_delivery, send_delivery, sync_bale_destinations
from .models import AutomationRun, AutomationSchedule, AutomationState, BaleDestination, CollectionRun, Delivery, Source, SystemSetting, Topic
from .trend_services import create_analysis_run, execute_analysis

LOCK_TTL=timedelta(minutes=30)


class LeaseLostError(Exception):
    pass


def _acquire(trigger, *, require_due, schedule=None):
    now=timezone.now()
    with transaction.atomic():
        setting=None
        if schedule is not None:
            schedule=AutomationSchedule.objects.select_for_update().get(pk=schedule.pk)
            if require_due and not schedule.is_active:
                return None
            if require_due and schedule.last_started_at and now < schedule.last_started_at+timedelta(hours=schedule.interval_hours):
                return None
        else:
            setting=SystemSetting.objects.select_for_update().get(pk=SystemSetting.DEFAULT_PK) if SystemSetting.objects.filter(pk=SystemSetting.DEFAULT_PK).exists() else SystemSetting.get_solo()
            if require_due and not setting.automation_enabled:
                return None
        state=AutomationState.objects.select_for_update().get_or_create(pk=1,defaults={"singleton":True})[0]
        if state.lock_expires_at and state.lock_expires_at>now:
            return AutomationRun.objects.create(schedule=schedule,trigger=trigger,status=AutomationRun.Status.SKIPPED,started_at=now,finished_at=now,error_summary="pipeline دیگری در حال اجرا است.")
        if schedule is None and require_due and state.last_started_at and now < state.last_started_at+timedelta(minutes=setting.automation_interval_minutes):
            return None
        owner=secrets.token_urlsafe(32)
        state.last_started_at=now;state.lock_expires_at=now+LOCK_TTL;state.lock_owner=owner;state.save(update_fields=["last_started_at","lock_expires_at","lock_owner","updated_at"])
        run = AutomationRun.objects.create(
            schedule=schedule,
            trigger=trigger,
            status=AutomationRun.Status.PENDING,
            lease_owner=owner,
            lookback_hours=schedule.lookback_hours if schedule is not None else 24,
        )
        if schedule is not None:
            selected_sources=schedule.sources.filter(is_active=True)
            selected_topics=schedule.topics.filter(is_active=True)
            schedule.last_started_at=now
            schedule.save(update_fields=["last_started_at","updated_at"])
        else:
            selected_sources = setting.automation_sources.filter(is_active=True) if setting.automation_sources_configured else Source.objects.filter(is_active=True)
            if not selected_sources.exists():
                selected_sources = Source.objects.filter(is_active=True)
            selected_topics = setting.automation_topics.filter(is_active=True) if setting.automation_topics_configured else Topic.objects.filter(is_active=True)
            if not selected_topics.exists():
                selected_topics = Topic.objects.filter(is_active=True)
        run.selected_sources.set(selected_sources)
        run.selected_topics.set(selected_topics)
        return run


def renew_lease(run):
    now=timezone.now()
    with transaction.atomic():
        state=AutomationState.objects.select_for_update().get(pk=1)
        if not run.lease_owner or state.lock_owner!=run.lease_owner or not state.lock_expires_at or state.lock_expires_at<=now:
            return False
        state.lock_expires_at=now+LOCK_TTL
        state.save(update_fields=["lock_expires_at","updated_at"])
        return True


def release_lease(run):
    with transaction.atomic():
        state=AutomationState.objects.select_for_update().get(pk=1)
        if not run.lease_owner or state.lock_owner!=run.lease_owner:
            return False
        state.lock_owner="";state.lock_expires_at=None
        state.save(update_fields=["lock_owner","lock_expires_at","updated_at"])
        return True


def _require_lease(run):
    if not renew_lease(run):
        raise LeaseLostError("مالکیت قفل pipeline از دست رفت.")


def dispatch_scheduled_pipeline():
    """Acquire the first active due schedule; each schedule owns its interval and scope."""
    now=timezone.now()
    for schedule in AutomationSchedule.objects.filter(is_active=True).order_by("last_started_at","id"):
        if schedule.last_started_at is None or now >= schedule.last_started_at+timedelta(hours=schedule.interval_hours):
            return _acquire(AutomationRun.Trigger.SCHEDULED,require_due=True,schedule=schedule)
    return None


def start_manual_pipeline():
    return _acquire(AutomationRun.Trigger.MANUAL,require_due=False)


def start_schedule_pipeline(schedule):
    return _acquire(AutomationRun.Trigger.MANUAL,require_due=False,schedule=schedule)


def _collection(platform, trigger, sources=None, topics=None, lookback_hours=None):
    end_at=timezone.now() if lookback_hours else None
    run=create_manual_run(
        platform,
        sources=sources,
        requested_start_at=end_at-timedelta(hours=lookback_hours) if end_at else None,
        requested_end_at=end_at,
    )
    run.run_type=CollectionRun.RunType.SCHEDULED if trigger==AutomationRun.Trigger.SCHEDULED else run.run_type
    run.save(update_fields=["run_type"])
    if topics is not None:
        run.selected_topics.set(topics)
    return execute_run(run)


def execute_pipeline(run_id, *, delivery_enqueuer=None):
    run=AutomationRun.objects.get(pk=run_id)
    if run.status in {AutomationRun.Status.SUCCESS,AutomationRun.Status.PARTIAL,AutomationRun.Status.FAILED,AutomationRun.Status.SKIPPED}:
        return run
    run.status=AutomationRun.Status.RUNNING;run.started_at=timezone.now();run.save(update_fields=["status","started_at"])
    partial=False;delivery_ids=[]
    try:
        _require_lease(run)
        selected_sources = run.selected_sources.filter(is_active=True)
        selected_topics = run.selected_topics.filter(is_active=True)
        try:
            telegram=_collection(Source.Platform.TELEGRAM,run.trigger,selected_sources,selected_topics,run.lookback_hours)
        except TypeError:
            try:
                telegram=_collection(Source.Platform.TELEGRAM,run.trigger,selected_sources)
            except TypeError:
                telegram=_collection(Source.Platform.TELEGRAM,run.trigger)
        run.telegram_run=telegram
        partial=partial or telegram.status==telegram.Status.PARTIAL
        _require_lease(run)
        try:
            x=_collection(Source.Platform.X,run.trigger,selected_sources,selected_topics,run.lookback_hours)
        except TypeError:
            try:
                x=_collection(Source.Platform.X,run.trigger,selected_sources)
            except TypeError:
                x=_collection(Source.Platform.X,run.trigger)
        run.x_run=x
        partial=partial or x.status==x.Status.PARTIAL
        _require_lease(run)
        trend=create_analysis_run();execute_analysis(trend, topics=run.selected_topics.filter(is_active=True));run.trend_analysis_run=trend
        _require_lease(run)
        try:
            sync_bale_destinations()
        except Exception:
            pass
        for destination in BaleDestination.objects.filter(is_active=True):
            clusters=trend_clusters_for_automation(destination, topics=selected_topics)
            for cluster in clusters:
                delivery,created=create_delivery(destination=destination,cluster=cluster)
                if not created:
                    continue
                run.deliveries_created+=1
                delivery_ids.append(delivery.pk)
                if delivery_enqueuer:
                    delivery_enqueuer(delivery.pk)
                else:
                    send_delivery(delivery.pk)
        run.status=AutomationRun.Status.PARTIAL if partial else AutomationRun.Status.SUCCESS
    except Exception as exc:
        run.refresh_from_db()
        if run.error_summary != "توسط کاربر متوقف شد":
            run.status=AutomationRun.Status.FAILED;run.error_summary=exc.__class__.__name__[:500]
    finally:
        current_run = AutomationRun.objects.filter(pk=run.pk).first()
        if current_run and current_run.error_summary == "توسط کاربر متوقف شد":
            run.error_summary = current_run.error_summary
            run.status = AutomationRun.Status.FAILED
        run.deliveries_delivered=Delivery.objects.filter(pk__in=delivery_ids,status=Delivery.Status.DELIVERED).count()
        run.deliveries_failed=Delivery.objects.filter(pk__in=delivery_ids,status=Delivery.Status.FAILED).count()
        run.finished_at=timezone.now();run.save()
        release_lease(run)
    return run


def trend_clusters_for_automation(destination, *, topics=None):
    existing=Delivery.objects.filter(destination=destination,report_version=1).values("trend_cluster_id")
    from .models import TrendCluster
    clusters = TrendCluster.objects.filter(status__in=[TrendCluster.Status.TRENDING,TrendCluster.Status.CONFIRMED],merged_into__isnull=True)
    if topics is not None:
        clusters = clusters.filter(topic__in=topics)
    return clusters.exclude(pk__in=existing)
