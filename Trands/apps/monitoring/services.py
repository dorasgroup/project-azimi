from django.db import transaction
from django.utils import timezone

from .models import AuditLog, AutomationRun, AutomationState, CollectionRun, ContentItem, TrendCluster


def record_audit(*, actor, operation, instance, metadata=None):
    """Store only minimal, non-sensitive auditing context for configuration changes."""
    safe_metadata = {"changed": True, **(metadata or {})}
    log = AuditLog(actor=actor if getattr(actor, "is_authenticated", False) else None, operation=operation, entity_type=instance._meta.label, entity_id=str(instance.pk), metadata=safe_metadata)
    log.full_clean()
    log.save()
    return log


def delete_single_content_item(item_id, *, actor):
    """Delete a single ContentItem and log audit."""
    try:
        item = ContentItem.objects.select_related("source").get(pk=item_id)
    except ContentItem.DoesNotExist:
        return False
    source_name = str(item.source.resolved_name if hasattr(item.source, "resolved_name") else item.source)
    record_audit(actor=actor, operation="content_item_delete", instance=item, metadata={"source": source_name, "external_id": item.external_id})
    item.delete()
    return True


def bulk_delete_content_items(item_ids, *, actor):
    """Delete multiple ContentItems by their primary keys."""
    queryset = ContentItem.objects.filter(pk__in=item_ids)
    count = queryset.count()
    if count == 0:
        return 0
    sample = queryset.first()
    record_audit(actor=actor, operation="content_item_bulk_delete", instance=sample, metadata={"count": count})
    queryset.delete()
    return count


def purge_content_items(*, actor, query=None, platform=None, source_id=None, date_from=None, date_to=None):
    """Purge all ContentItems or those matching the active filter criteria."""
    queryset = ContentItem.objects.all()
    if query:
        queryset = queryset.filter(normalized_text__icontains=query)
    if platform:
        queryset = queryset.filter(source__platform=platform)
    if source_id:
        queryset = queryset.filter(source_id=source_id)
    if date_from:
        queryset = queryset.filter(published_at__date__gte=date_from)
    if date_to:
        queryset = queryset.filter(published_at__date__lte=date_to)

    count = queryset.count()
    if count > 0:
        sample = queryset.first()
        record_audit(actor=actor, operation="content_item_purge", instance=sample, metadata={"purged_count": count})
        queryset.delete()
    return count


def delete_single_trend_cluster(cluster_id, *, actor):
    """Delete a single TrendCluster and log audit."""
    try:
        cluster = TrendCluster.objects.get(pk=cluster_id)
    except TrendCluster.DoesNotExist:
        return False
    cluster.deliveries.all().delete()
    record_audit(
        actor=actor,
        operation="trend_cluster_delete",
        instance=cluster,
        metadata={"score": cluster.score, "source_count": cluster.independent_source_count},
    )
    cluster.delete()
    return True


def bulk_delete_trend_clusters(cluster_ids, *, actor):
    """Delete multiple TrendClusters by their primary keys."""
    queryset = TrendCluster.objects.filter(pk__in=cluster_ids)
    count = queryset.count()
    if count == 0:
        return 0
    sample = queryset.first()
    record_audit(actor=actor, operation="trend_cluster_bulk_delete", instance=sample, metadata={"count": count})
    for cluster in queryset:
        cluster.deliveries.all().delete()
    queryset.delete()
    return count


def delete_single_automation_run(run_id, *, actor):
    """Delete a single terminal AutomationRun and log audit."""
    try:
        run = AutomationRun.objects.get(pk=run_id)
    except AutomationRun.DoesNotExist:
        return False
    if run.status in (AutomationRun.Status.PENDING, AutomationRun.Status.RUNNING):
        return False
    record_audit(actor=actor, operation="automation_run_delete", instance=run, metadata={"trigger": run.trigger, "status": run.status})
    run.delete()
    return True


def bulk_delete_automation_runs(run_ids, *, actor):
    """Delete multiple terminal AutomationRuns by their primary keys."""
    queryset = AutomationRun.objects.filter(pk__in=run_ids).exclude(
        status__in=(AutomationRun.Status.PENDING, AutomationRun.Status.RUNNING)
    )
    count = queryset.count()
    if count == 0:
        return 0
    sample = queryset.first()
    record_audit(actor=actor, operation="automation_run_bulk_delete", instance=sample, metadata={"count": count})
    queryset.delete()
    return count


def stop_automation_run(run_id, *, actor):
    """Stop an in-progress (PENDING or RUNNING) AutomationRun, release lease, and log audit."""
    try:
        run = AutomationRun.objects.get(pk=run_id)
    except AutomationRun.DoesNotExist:
        return False
    if run.status not in (AutomationRun.Status.PENDING, AutomationRun.Status.RUNNING):
        return False

    with transaction.atomic():
        state = AutomationState.objects.select_for_update().get_or_create(pk=1, defaults={"singleton": True})[0]
        if run.lease_owner and state.lock_owner == run.lease_owner:
            state.lock_owner = ""
            state.lock_expires_at = None
            state.save(update_fields=["lock_owner", "lock_expires_at", "updated_at"])

        now = timezone.now()
        run.status = AutomationRun.Status.FAILED
        run.error_summary = "توسط کاربر متوقف شد"
        if not run.started_at:
            run.started_at = now
        run.finished_at = now
        run.save(update_fields=["status", "error_summary", "started_at", "finished_at"])

        if run.telegram_run and run.telegram_run.status in (CollectionRun.Status.PENDING, CollectionRun.Status.RUNNING):
            run.telegram_run.status = CollectionRun.Status.FAILED
            run.telegram_run.finished_at = now
            run.telegram_run.error_summary = "توسط کاربر متوقف شد"
            run.telegram_run.save(update_fields=["status", "finished_at", "error_summary"])

        if run.x_run and run.x_run.status in (CollectionRun.Status.PENDING, CollectionRun.Status.RUNNING):
            run.x_run.status = CollectionRun.Status.FAILED
            run.x_run.finished_at = now
            run.x_run.error_summary = "توسط کاربر متوقف شد"
            run.x_run.save(update_fields=["status", "finished_at", "error_summary"])

        record_audit(actor=actor, operation="automation_run_stop", instance=run, metadata={"trigger": run.trigger, "stopped": True})
    return True


