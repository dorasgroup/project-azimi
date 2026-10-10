from django.db import transaction
from django.utils import timezone
from datetime import timedelta
from .collectors import CollectionResult, TelegramCollector
from .models import CollectionCursor, CollectionRun, CollectionRunSource, ContentItem, Source
from .text_normalization import normalize_persian_text
from .trend_services import is_message_matching_topics, refresh_keyword_matches
from .x_collectors import XCollectorError, XRecentSearchCollector
from .services import record_audit


def _safe_error(exc):
    if isinstance(exc, XCollectorError):
        return exc.safe_message[:300]
    return f"{exc.__class__.__name__}"[:300]


def create_manual_run(platform=Source.Platform.TELEGRAM, *, sources=None, run_type=CollectionRun.RunType.MANUAL, requested_start_at=None, requested_end_at=None):
    sources = sources if sources is not None else Source.objects.all()
    sources = sources.filter(is_active=True)
    if platform:
        sources = sources.filter(platform=platform)
    run = CollectionRun.objects.create(run_type=run_type, status=CollectionRun.Status.PENDING, target_sources_count=sources.count(), requested_start_at=requested_start_at, requested_end_at=requested_end_at)
    CollectionRunSource.objects.bulk_create([CollectionRunSource(collection_run=run, source=source) for source in sources])
    return run


def create_recent_days_run(*, sources, days, topics=None):
    end_at = timezone.now()
    run = create_manual_run(
        platform=None,
        sources=sources,
        run_type=CollectionRun.RunType.RECENT_DAYS,
        requested_start_at=end_at - timedelta(days=days),
        requested_end_at=end_at,
    )
    if topics is not None:
        run.selected_topics.set(topics)
    return run


def _collector_for_source(source):
    if source.platform == Source.Platform.X:
        return XRecentSearchCollector()
    return TelegramCollector()


def _cancel_run(run, *, current_result=None):
    """Finish a cooperative cancellation without touching collected content or cursors."""
    now = timezone.now()
    pending_results = run.source_results.filter(
        status__in=(CollectionRunSource.Status.PENDING, CollectionRunSource.Status.RUNNING)
    )
    if current_result is not None:
        pending_results = pending_results.filter(pk=current_result.pk)
    pending_results.update(status=CollectionRunSource.Status.CANCELLED, finished_at=now)
    if current_result is not None:
        run.source_results.filter(status__in=(CollectionRunSource.Status.PENDING, CollectionRunSource.Status.RUNNING)).exclude(pk=current_result.pk).update(
            status=CollectionRunSource.Status.CANCELLED, finished_at=now
        )
    run.status = CollectionRun.Status.CANCELLED
    run.finished_at = now
    run.save(update_fields=("status", "finished_at"))
    return run


def request_cancel(run, *, actor, force=False):
    """Request an idempotent cooperative cancellation for a collection run.

    If force is True, or if a cancellation was already requested and is still
    active, complete the cancellation immediately so runs never remain stuck.
    """
    with transaction.atomic():
        run = CollectionRun.objects.select_for_update().get(pk=run.pk)
        if run.is_terminal:
            return run, False
        if run.status == CollectionRun.Status.PENDING or force:
            run.cancel_requested_at = run.cancel_requested_at or timezone.now()
            run.save(update_fields=("cancel_requested_at",))
            _cancel_run(run)
            record_audit(actor=actor, operation="collection_run_cancel", instance=run, metadata={"mode": "immediate" if not force else "forced"})
            return run, True
        if run.cancel_requested_at:
            _cancel_run(run)
            record_audit(actor=actor, operation="collection_run_cancel", instance=run, metadata={"mode": "forced"})
            return run, True
        run.cancel_requested_at = timezone.now()
        run.save(update_fields=("cancel_requested_at",))
        record_audit(actor=actor, operation="collection_run_cancel", instance=run, metadata={"mode": "cooperative"})
        return run, True


def delete_terminal_run(run, *, actor):
    """Delete only a completed run and its result rows; related content remains intact."""
    with transaction.atomic():
        try:
            run = CollectionRun.objects.select_for_update().get(pk=run.pk)
        except CollectionRun.DoesNotExist:
            return True
        if not run.is_terminal:
            return False
        record_audit(actor=actor, operation="collection_run_delete", instance=run, metadata={"status": run.status})
        run.delete()
        return True


def _update_existing_content(item, content):
    new_values = {
        "raw_text": item.raw_text,
        "normalized_text": normalize_persian_text(item.raw_text),
        "published_at": item.published_at,
        "public_url": item.public_url,
        "content_type": item.content_type,
        "media_url": item.media_url,
        "metadata": item.metadata or {},
    }
    changed_fields = [field for field, value in new_values.items() if getattr(content, field) != value]
    if changed_fields:
        for field in changed_fields:
            setattr(content, field, new_values[field])
        content.save(update_fields=changed_fields + ["updated_at"])
        return True
    return False


def execute_run(run, collector=None):
    run.refresh_from_db()
    if run.is_terminal:
        return run
    if run.cancel_requested_at:
        return _cancel_run(run)
    run.status, run.started_at = CollectionRun.Status.RUNNING, timezone.now()
    run.save(update_fields=("status", "started_at"))
    for result in run.source_results.select_related("source"):
        run.refresh_from_db(fields=("status", "cancel_requested_at", "finished_at"))
        if run.is_terminal or run.cancel_requested_at:
            return _cancel_run(run)
        result.status, result.started_at = CollectionRunSource.Status.RUNNING, timezone.now()
        result.save(update_fields=("status", "started_at"))
        cursor, _ = CollectionCursor.objects.get_or_create(source=result.source)
        use_incremental_cursor = run.run_type != CollectionRun.RunType.RECENT_DAYS
        active_cursor = cursor if use_incremental_cursor else CollectionCursor(source=result.source)
        start_at = run.requested_start_at
        if run.run_type == CollectionRun.RunType.SCHEDULED and run.requested_end_at:
            previous_end = CollectionRunSource.objects.filter(
                source=result.source,
                status=CollectionRunSource.Status.SUCCESS,
                collection_run__run_type=CollectionRun.RunType.SCHEDULED,
                collection_run__requested_end_at__lte=run.requested_end_at,
            ).exclude(collection_run=run).order_by("-collection_run__requested_end_at").values_list(
                "collection_run__requested_end_at", flat=True
            ).first()
            start_at = previous_end or start_at
        try:
            active_collector = collector or _collector_for_source(result.source)
            if start_at and hasattr(active_collector, "collect_window"):
                collection_result = active_collector.collect_window(result.source, active_cursor, start_at, run.requested_end_at)
            else:
                collection_result = active_collector.collect(result.source, active_cursor)
            run.refresh_from_db(fields=("status", "cancel_requested_at", "finished_at"))
            if run.is_terminal or run.cancel_requested_at:
                return _cancel_run(run, current_result=result)
            if isinstance(collection_result, list):
                newest = max(collection_result, key=lambda item: item.published_at, default=None)
                collection_result = CollectionResult(
                    messages=collection_result,
                    safe_cursor_external_id=newest.external_id if newest else "",
                    safe_cursor_published_at=newest.published_at if newest else None,
                )
            messages = collection_result.messages
            if start_at:
                end_at = run.requested_end_at or timezone.now()
                messages = [item for item in messages if start_at <= item.published_at <= end_at]
            scoped_topics = list(run.selected_topics.filter(is_active=True).prefetch_related("keywords"))
            if scoped_topics:
                messages = [item for item in messages if is_message_matching_topics(item.raw_text, scoped_topics)]
            received = created = duplicates = updated = 0
            with transaction.atomic():
                for item in messages:
                    received += 1
                    content, was_created = ContentItem.objects.get_or_create(source=result.source, external_id=item.external_id, defaults={"published_at": item.published_at, "raw_text": item.raw_text, "normalized_text": normalize_persian_text(item.raw_text), "public_url": item.public_url, "content_type": item.content_type, "media_url": item.media_url, "metadata": item.metadata or {}})
                    created += int(was_created); duplicates += int(not was_created)
                    if not was_created and collection_result.update_existing:
                        updated += int(_update_existing_content(item, content))
                    refresh_keyword_matches([content])
                if use_incremental_cursor and collection_result.safe_cursor_external_id:
                    cursor.last_external_id = collection_result.safe_cursor_external_id
                    cursor.last_published_at = collection_result.safe_cursor_published_at
                if use_incremental_cursor:
                    cursor.last_successful_run_at = timezone.now(); cursor.save()
                if collection_result.discovered_name and result.source.discovered_name != collection_result.discovered_name:
                    result.source.discovered_name = collection_result.discovered_name
                    result.source.save(update_fields=("discovered_name",))
            result.status = CollectionRunSource.Status.PARTIAL if collection_result.parse_error_count else CollectionRunSource.Status.SUCCESS
            result.received_count, result.new_count, result.duplicate_count, result.updated_count = received, created, duplicates, updated
            result.parse_error_count = collection_result.parse_error_count
            if collection_result.parse_error_count:
                result.error_summary = f"parser: {collection_result.parse_error_count} malformed post(s) skipped"
        except Exception as exc:
            result.status, result.error_summary = CollectionRunSource.Status.FAILED, _safe_error(exc)
        result.finished_at = timezone.now(); result.save()
    results = run.source_results.all()
    run.received_content_count = sum(item.received_count for item in results); run.new_content_count = sum(item.new_count for item in results); run.duplicate_content_count = sum(item.duplicate_count for item in results); run.updated_content_count = sum(item.updated_count for item in results)
    run.refresh_from_db(fields=("status", "cancel_requested_at", "finished_at"))
    if run.is_terminal or run.cancel_requested_at:
        return _cancel_run(run)
    run.status = CollectionRun.Status.PARTIAL if results.filter(status__in=(CollectionRunSource.Status.FAILED, CollectionRunSource.Status.PARTIAL)).exists() else CollectionRun.Status.SUCCESS
    run.finished_at = timezone.now(); run.save()
    return run
