from celery import shared_task
from django.conf import settings
import threading
from .collection_services import execute_run
from .models import CollectionRun
from .trend_services import create_analysis_run, execute_analysis
from .models import TrendAnalysisRun
from .delivery_services import send_delivery
from .automation_services import dispatch_scheduled_pipeline, execute_pipeline
from .retention_services import cleanup_retention as cleanup_retention_service


def _run_in_dev_thread_or_sync(func, *args):
    if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False) and getattr(settings, "DEV_ASYNC_THREAD", False):
        t = threading.Thread(target=func, args=args, daemon=True)
        t.start()
        return None
    return func(*args)


@shared_task
def execute_collection_run(run_id):
    def _execute():
        run = CollectionRun.objects.get(pk=run_id)
        execute_run(run)
        topics = run.selected_topics.filter(is_active=True)
        if not topics.exists():
            from .models import Topic
            topics = Topic.objects.filter(is_active=True)
        if topics.exists():
            analysis_run = create_analysis_run()
            execute_analysis(
                analysis_run,
                topics=topics,
                since=run.requested_start_at,
            )
            from .models import TrendCluster
            from .delivery_services import dispatch_bale_deliveries
            run_trends = TrendCluster.objects.filter(
                topic__in=topics,
                merged_into__isnull=True,
                status__in=[TrendCluster.Status.TRENDING, TrendCluster.Status.CONFIRMED],
            )
            if run.requested_start_at:
                run_trends = run_trends.filter(last_published_at__gte=run.requested_start_at)
            is_manual = run.run_type in (CollectionRun.RunType.MANUAL, CollectionRun.RunType.RECENT_DAYS)
            dispatch_bale_deliveries(clusters=run_trends if is_manual else None, force_resend=is_manual)
        return run.pk
    return _run_in_dev_thread_or_sync(_execute)


@shared_task
def execute_trend_analysis(run_id):
    def _execute():
        run = TrendAnalysisRun.objects.get(pk=run_id)
        result = execute_analysis(run)
        from .delivery_services import dispatch_bale_deliveries
        dispatch_bale_deliveries()
        return result.pk
    return _run_in_dev_thread_or_sync(_execute)


@shared_task
def execute_bale_delivery(delivery_id):
    def _execute():
        return send_delivery(delivery_id).pk
    return _run_in_dev_thread_or_sync(_execute)


@shared_task
def dispatch_automation():
    run = dispatch_scheduled_pipeline()
    if run and run.status == run.Status.PENDING:
        execute_automation_pipeline.delay(run.pk)
    return run.pk if run else None


@shared_task
def execute_automation_pipeline(run_id):
    def _execute():
        return execute_pipeline(run_id).pk
    return _run_in_dev_thread_or_sync(_execute)


@shared_task
def cleanup_retention():
    return cleanup_retention_service()


@shared_task
def execute_idea_delivery(delivery_id):
    from .idea_delivery_services import send_idea_delivery

    return send_idea_delivery(delivery_id).pk


@shared_task
def sync_idea_delivery_status(delivery_id):
    from .idea_delivery_services import sync_idea_delivery

    return sync_idea_delivery(delivery_id).pk


@shared_task
def maintain_idea_deliveries():
    from .idea_delivery_services import maintain_idea_outbox

    return maintain_idea_outbox(
        send_enqueuer=execute_idea_delivery.delay,
        sync_enqueuer=sync_idea_delivery_status.delay,
    )
