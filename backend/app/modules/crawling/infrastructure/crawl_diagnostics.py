"""Build one typed diagnostic summary from a completed crawl attempt."""

from collections import Counter
from datetime import date, datetime

from app.crawler.schemas import CrawlDiagnostics, CrawledPage, DiscoveredLink, SourceProbe


class CrawlDiagnosticsBuilder:
    """Classify discovery, selection and fetch outcomes without performing I/O."""

    @staticmethod
    def build(
        *,
        probe: SourceProbe,
        discovered: list[DiscoveredLink],
        selected: list[DiscoveredLink],
        pages: list[CrawledPage],
        date_from: date | None,
        date_to: date | None,
        max_pages: int,
        started_at: datetime,
        finished_at: datetime,
    ) -> CrawlDiagnostics:
        provider_counts: Counter[str] = Counter()
        for link in discovered:
            provider_counts.update(link.discovery_methods or [link.discovered_via])
        selected_provider_counts: Counter[str] = Counter()
        for link in selected:
            selected_provider_counts.update(
                link.discovery_methods or [link.discovered_via]
            )

        eligible_count = sum(
            link.published_at is None
            or (
                (date_from is None or link.published_at.date() >= date_from)
                and (date_to is None or link.published_at.date() <= date_to)
            )
            for link in discovered
        )
        rejected_by_date = len(discovered) - eligible_count
        skipped_by_limit = max(0, eligible_count - min(eligible_count, max_pages))
        strategy_counts = Counter(
            str(page.metadata.get("fetch_strategy") or "unknown") for page in pages
        )
        failure_counts: Counter[str] = Counter()
        status_counts: Counter[str] = Counter()
        pending_pages = sum(page.metadata.get('failure_kind') == 'pending' for page in pages)
        retryable_pages = sum(not page.success and page.metadata.get('failure_kind') != 'pending' and bool(page.metadata.get('retryable')) for page in pages)
        for page in pages:
            if page.status_code is not None:
                status_counts[str(page.status_code)] += 1
            if not page.success and page.metadata.get('failure_kind') != 'pending':
                failure_counts[
                    str(page.metadata.get("failure_kind") or "unclassified")
                ] += 1

        successful_pages = sum(page.success for page in pages)
        status, reason_code, summary = CrawlDiagnosticsBuilder._classify(
            probe=probe,
            discovered_count=len(discovered),
            selected_count=len(selected),
            page_count=len(pages),
            successful_pages=successful_pages,
            rejected_by_date=rejected_by_date,
        )
        if selected and (pending_pages or retryable_pages):
            status, reason_code = 'partial', 'crawl_continuation_required'
        if selected:
            summary += f' دریافت موفق: {successful_pages} از {len(selected)}؛ خطای دریافت: {len(pages) - successful_pages - pending_pages}؛ در صف: {pending_pages}؛ قابل تلاش مجدد: {retryable_pages}.'
            if failure_counts:
                labels = {'transport': 'مهلت/اتصال درخواست', 'transient_http': 'خطای موقت یا محدودیت نرخ',
                    'access_restricted': 'محدودیت دسترسی سایت', 'robots': 'ممنوعیت robots.txt',
                    'validation': 'دامنه یا تغییر مسیر غیرمجاز', 'circuit_open': 'توقف موقت درخواست‌ها',
                    'permanent_http': 'خطای HTTP پایدار', 'render_unavailable': 'نیاز به رندر در مسیر مجاز'}
                summary += ' علت‌ها: ' + '، '.join(f'{labels.get(kind, kind)} ({count})' for kind, count in failure_counts.items()) + '.'
            if skipped_by_limit:
                summary += f' {skipped_by_limit} نشانی واجد شرایط خارج از سقف انتخاب بود؛ پوشش کل سایت تضمین نشده است.'
        return CrawlDiagnostics(
            status=status,
            reason_code=reason_code,
            summary=summary,
            provider_counts=dict(provider_counts),
            selected_provider_counts=dict(selected_provider_counts),
            discovered_total=len(discovered),
            selected_total=len(selected),
            rejected_by_date=rejected_by_date,
            skipped_by_limit=skipped_by_limit,
            fetched_total=len(pages) - pending_pages,
            successful_pages=successful_pages,
            failed_pages=len(pages) - successful_pages - pending_pages,
            pending_pages=pending_pages,
            retryable_pages=retryable_pages,
            resumed_pages=sum(bool(page.metadata.get('checkpoint_hit')) for page in pages),
            selected_completion_percent=round(100 * successful_pages / len(selected), 1) if selected else 0,
            fetch_strategy_counts=dict(strategy_counts),
            failure_counts=dict(failure_counts),
            http_status_counts=dict(status_counts),
            duration_ms=max(0, round((finished_at - started_at).total_seconds() * 1000)),
        )

    @staticmethod
    def _classify(
        *,
        probe: SourceProbe,
        discovered_count: int,
        selected_count: int,
        page_count: int,
        successful_pages: int,
        rejected_by_date: int,
    ) -> tuple[str, str, str]:
        if not probe.success:
            return "failed", "probe_failed", probe.reason or "منبع در دسترس نبود."
        if probe.robots_allowed is False:
            return "blocked", "robots_blocked", "خزش منبع توسط robots.txt مسدود شد."
        if discovered_count == 0:
            return (
                "empty",
                "no_urls_discovered",
                "هیچ URL خبری از RSS، sitemap، جست‌وجو، آرشیو یا index تاریخی پیدا نشد.",
            )
        if selected_count == 0 and rejected_by_date:
            return (
                "empty",
                "all_urls_outside_date_range",
                "URL کشف شد، اما همه نتایج تاریخ‌دار خارج از بازه رصد بودند.",
            )
        if page_count == 0:
            return "empty", "no_pages_fetched", "URL انتخاب شد، اما هیچ صفحه‌ای دریافت نشد."
        if successful_pages == 0:
            return "failed", "all_fetches_failed", "دریافت تمام صفحه‌های انتخاب‌شده ناموفق بود."
        if successful_pages < page_count:
            return "partial", "partial_fetch_success", "بخشی از صفحه‌ها با موفقیت دریافت شدند."
        return "success", "completed", "خزش و دریافت صفحه‌ها با موفقیت انجام شد."
