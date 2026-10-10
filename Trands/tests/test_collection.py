from datetime import timedelta
import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from apps.monitoring.collectors import CollectedMessage, CollectionResult
from apps.monitoring.collection_services import create_manual_run, create_recent_days_run, delete_terminal_run, execute_run, request_cancel
from apps.monitoring.models import AuditLog, CollectionCursor, CollectionRun, CollectionRunSource, ContentItem, Keyword, KeywordMatch, Source, Topic
from apps.monitoring.tasks import execute_collection_run


class FakeCollector:
    def __init__(self, messages=None, failing=None): self.messages, self.failing = messages or {}, failing or set()
    def collect(self, source, cursor):
        if source.pk in self.failing: raise RuntimeError("sensitive token must not leak")
        return self.messages.get(source.pk, [])


def telegram_source(identifier="channel", **kwargs):
    display_name = kwargs.pop("display_name", "کانال")
    return Source.objects.create(
        platform="telegram",
        display_name=display_name,
        public_url=f"https://t.me/s/{identifier}",
        **kwargs,
    )


@pytest.fixture
def staff(db):
    return get_user_model().objects.create_user("collection-operator", password="Strong-pass-42", is_staff=True)


@pytest.mark.django_db
def test_collection_is_idempotent_and_advances_cursor():
    source = telegram_source()
    message = CollectedMessage("10", timezone.now() - timedelta(hours=1), "caption", "https://t.me/channel/10", "media", {"has_media": True})
    run = create_manual_run(); execute_run(run, FakeCollector({source.pk: [message]}))
    assert ContentItem.objects.get().raw_text == "caption"
    assert CollectionCursor.objects.get(source=source).last_external_id == "10"
    second = create_manual_run(); execute_run(second, FakeCollector({source.pk: [message]}))
    assert ContentItem.objects.count() == 1
    assert second.duplicate_content_count == 1


@pytest.mark.django_db
def test_failed_source_does_not_move_cursor_or_stop_others():
    failed = telegram_source("bad_channel", display_name="bad")
    good = telegram_source("good_channel", display_name="good")
    message = CollectedMessage("2", timezone.now(), "text")
    run = create_manual_run(); execute_run(run, FakeCollector({good.pk: [message]}, {failed.pk}))
    assert not CollectionCursor.objects.filter(source=failed, last_external_id__gt="").exists()
    assert ContentItem.objects.filter(source=good).exists()
    assert run.source_results.filter(status=CollectionRunSource.Status.FAILED).count() == 1
    assert run.status == "partial"


@pytest.mark.django_db
def test_inactive_and_non_telegram_sources_are_ignored():
    telegram_source("off_channel", is_active=False)
    Source.objects.create(platform="x", display_name="x", identifier="x")
    run = create_manual_run()
    assert run.target_sources_count == 0
    assert run.source_results.count() == 0


@pytest.mark.django_db
def test_task_runs_with_celery_eager(monkeypatch):
    source = telegram_source("channel_a")
    run = create_manual_run()
    monkeypatch.setattr("apps.monitoring.collection_services.TelegramCollector", lambda: FakeCollector({source.pk: []}))
    assert execute_collection_run.delay(run.pk).get() == run.pk


@pytest.mark.django_db
def test_partial_parser_result_stores_good_posts_without_advancing_past_bad_post():
    source = telegram_source()
    first = CollectedMessage("10", timezone.now() - timedelta(minutes=2), "متن يک")
    later = CollectedMessage("12", timezone.now(), "متن دوم")

    class PartialCollector:
        def collect(self, source, cursor):
            return CollectionResult(
                messages=[first, later],
                discovered_name="نام کشف‌شده",
                parse_error_count=1,
                safe_cursor_external_id="10",
                safe_cursor_published_at=first.published_at,
            )

    run = create_manual_run()
    execute_run(run, PartialCollector())
    result = run.source_results.get()
    assert ContentItem.objects.count() == 2
    assert ContentItem.objects.get(external_id="10").normalized_text == "متن یک"
    source.refresh_from_db()
    assert source.discovered_name == "نام کشف‌شده"
    assert CollectionCursor.objects.get(source=source).last_external_id == "10"
    assert result.status == CollectionRunSource.Status.PARTIAL
    assert result.parse_error_count == 1
    assert run.status == "partial"


@pytest.mark.django_db
def test_recent_days_run_uses_only_selected_sources_filters_messages_and_preserves_cursor():
    selected = telegram_source("selected")
    ignored = telegram_source("ignored")
    cursor = CollectionCursor.objects.create(source=selected, last_external_id="91")
    now = timezone.now()
    run = create_recent_days_run(sources=Source.objects.filter(pk=selected.pk), days=1)

    class RangeCollector:
        def collect(self, source, cursor):
            assert source == selected
            return [
                CollectedMessage("90", now - timedelta(days=2), "قدیمی"),
                CollectedMessage("92", now - timedelta(hours=2), "تازه"),
            ]

    execute_run(run, RangeCollector())
    cursor.refresh_from_db()
    assert run.run_type == "recent_days"
    assert run.source_results.count() == 1
    assert not ContentItem.objects.filter(source=ignored).exists()
    assert list(ContentItem.objects.values_list("external_id", flat=True)) == ["92"]
    assert cursor.last_external_id == "91"


@pytest.mark.django_db
def test_scheduled_collection_resumes_at_previous_successful_end_for_each_source():
    source = telegram_source("scheduled_channel")
    now = timezone.now()
    first_start = now - timedelta(days=2)
    first_end = now - timedelta(days=1, minutes=5)
    first = create_manual_run(
        sources=Source.objects.filter(pk=source.pk),
        run_type=CollectionRun.RunType.SCHEDULED,
        requested_start_at=first_start,
        requested_end_at=first_end,
    )

    class WindowCollector:
        def __init__(self):
            self.starts = []

        def collect_window(self, source, cursor, start_at, end_at):
            self.starts.append(start_at)
            return CollectionResult(messages=[])

    collector = WindowCollector()
    execute_run(first, collector)
    assert first.source_results.get().status == CollectionRunSource.Status.SUCCESS

    second = create_manual_run(
        sources=Source.objects.filter(pk=source.pk),
        run_type=CollectionRun.RunType.SCHEDULED,
        requested_start_at=now - timedelta(days=1),
        requested_end_at=now,
    )
    execute_run(second, collector)

    assert collector.starts == [first_start, first_end]


@pytest.mark.django_db
def test_recent_days_run_keeps_selected_sources_across_platforms():
    telegram = telegram_source("selectedtg")
    x_source = Source.objects.create(platform="x", display_name="X رسانه", identifier="selectedx")
    ignored = telegram_source("ignoredtg")
    now = timezone.now()
    run = create_recent_days_run(sources=Source.objects.filter(pk__in=[telegram.pk, x_source.pk]), days=3)

    class MultiPlatformCollector:
        def collect(self, source, cursor):
            return [CollectedMessage(str(source.pk), now - timedelta(hours=3), f"متن {source.resolved_name}")]

    execute_run(run, MultiPlatformCollector())

    assert run.source_results.count() == 2
    assert set(run.source_results.values_list("source_id", flat=True)) == {telegram.pk, x_source.pk}
    assert set(ContentItem.objects.values_list("source_id", flat=True)) == {telegram.pk, x_source.pk}
    assert not ContentItem.objects.filter(source=ignored).exists()


@pytest.mark.django_db
def test_recent_days_run_snapshots_selected_topics():
    source = telegram_source("topic_snapshot")
    selected = Topic.objects.create(name="موضوع انتخابی")
    other = Topic.objects.create(name="موضوع دیگر")

    run = create_recent_days_run(
        sources=Source.objects.filter(pk=source.pk), days=1, topics=Topic.objects.filter(pk=selected.pk)
    )

    assert list(run.selected_topics.values_list("pk", flat=True)) == [selected.pk]
    assert not run.selected_topics.filter(pk=other.pk).exists()


@pytest.mark.django_db
def test_recent_days_run_filters_messages_by_selected_topic_keywords_and_populates_matches():
    source = telegram_source("politic_channel")
    topic = Topic.objects.create(name="سیاست")
    Keyword.objects.create(topic=topic, phrase="آمریکا", match_type="contains")
    Keyword.objects.create(topic=topic, phrase="شایعه", match_type="contains", is_excluded=True)

    now = timezone.now()
    run = create_recent_days_run(
        sources=Source.objects.filter(pk=source.pk),
        days=1,
        topics=Topic.objects.filter(pk=topic.pk),
    )

    class TopicCollector:
        def collect(self, src, cursor):
            return [
                CollectedMessage("1", now - timedelta(hours=1), "متن خبری درباره آمریکا و روابط بین‌الملل"),
                CollectedMessage("2", now - timedelta(hours=2), "معرفی خودرو مرسدس بنز جدید و بازار خودرو"),
                CollectedMessage("3", now - timedelta(hours=3), "شایعه جدید درباره آمریکا که مستثنا است"),
            ]

    execute_run(run, TopicCollector())

    items = list(ContentItem.objects.filter(source=source))
    assert len(items) == 1
    assert items[0].external_id == "1"
    assert KeywordMatch.objects.filter(content_item=items[0], keyword__phrase="آمریکا").exists()
    assert run.source_results.get().received_count == 1
    assert run.source_results.get().new_count == 1


@pytest.mark.django_db
def test_collection_progress_counts_terminal_source_results_and_clamps():
    first, second, third = telegram_source("progressa"), telegram_source("progressb"), telegram_source("progressc")
    run = create_manual_run(sources=Source.objects.filter(pk__in=[first.pk, second.pk, third.pk]), platform=None)
    results = list(run.source_results.order_by("pk"))
    results[0].status = CollectionRunSource.Status.SUCCESS
    results[0].save(update_fields=["status"])
    results[1].status = CollectionRunSource.Status.FAILED
    results[1].save(update_fields=["status"])
    assert run.progress_completed_count == 2
    assert run.progress_percent == 66
    run.target_sources_count = 1
    assert run.progress_percent == 100


@pytest.mark.django_db
def test_request_cancel_is_immediate_for_pending_and_idempotent(staff):
    source = telegram_source("pendingcancel")
    run = create_manual_run(sources=Source.objects.filter(pk=source.pk), platform=None)
    cancelled, changed = request_cancel(run, actor=staff)
    cancelled.refresh_from_db()
    assert changed and cancelled.status == CollectionRun.Status.CANCELLED
    assert cancelled.finished_at is not None
    assert cancelled.source_results.get().status == CollectionRunSource.Status.CANCELLED
    again, changed_again = request_cancel(cancelled, actor=staff)
    assert not changed_again and again.status == CollectionRun.Status.CANCELLED
    assert AuditLog.objects.filter(operation="collection_run_cancel", entity_id=str(run.pk)).count() == 1


@pytest.mark.django_db
def test_running_cancel_stops_between_sources_without_touching_next_cursor_or_content(staff):
    first, second = telegram_source("bridgefirst"), telegram_source("bridgesecond")
    run = create_manual_run(sources=Source.objects.filter(pk__in=[first.pk, second.pk]), platform=None)

    class CancellingCollector:
        calls = 0
        def collect(self, source, cursor):
            self.calls += 1
            if self.calls == 1:
                request_cancel(run, actor=staff)
            return [CollectedMessage(str(self.calls), timezone.now(), "متن")]

    execute_run(run, CancellingCollector())
    run.refresh_from_db()
    assert run.status == CollectionRun.Status.CANCELLED
    assert not ContentItem.objects.exists()
    assert not CollectionCursor.objects.filter(source=second).exists()
    assert run.source_results.filter(status=CollectionRunSource.Status.CANCELLED).count() == 2


@pytest.mark.django_db
def test_delete_terminal_run_only_removes_run_and_source_results(staff):
    source = telegram_source("deleterun")
    cursor = CollectionCursor.objects.create(source=source, last_external_id="5")
    content = ContentItem.objects.create(source=source, external_id="8", published_at=timezone.now())
    run = create_manual_run(sources=Source.objects.filter(pk=source.pk), platform=None)
    run.status = CollectionRun.Status.SUCCESS
    run.save(update_fields=["status"])
    assert delete_terminal_run(run, actor=staff)
    assert not CollectionRun.objects.filter(pk=run.pk).exists()
    assert ContentItem.objects.filter(pk=content.pk).exists()
    assert Source.objects.filter(pk=source.pk).exists()
    assert CollectionCursor.objects.filter(pk=cursor.pk).exists()
    assert AuditLog.objects.filter(operation="collection_run_delete", entity_id=str(run.pk)).exists()


@pytest.mark.django_db
def test_delete_pending_run_is_rejected(staff):
    source = telegram_source("keepactive")
    run = create_manual_run(sources=Source.objects.filter(pk=source.pk), platform=None)
    assert not delete_terminal_run(run, actor=staff)
    assert CollectionRun.objects.filter(pk=run.pk).exists()
