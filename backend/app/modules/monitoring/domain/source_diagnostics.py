"""Final per-source diagnosis across crawling and document preparation."""

from collections import Counter
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class SourceExecutionFacts:
    """Framework-independent facts collected by the monitoring pipeline."""

    crawl_status: str
    crawl_reason_code: str
    crawl_summary: str
    discovered_total: int
    selected_total: int
    fetched_total: int
    successful_pages: int
    provider_counts: Mapping[str, int]
    selected_provider_counts: Mapping[str, int]
    prepared_documents: int
    rejection_reasons: tuple[str, ...]
    duplicate_count: int


@dataclass(frozen=True)
class SourceExecutionDiagnostic:
    """Serializable final reason and bounded evidence for one source."""

    status: str
    reason_code: str
    summary: str
    recommended_action: str
    crawl_status: str
    crawl_reason_code: str
    stage_counts: Mapping[str, int]
    provider_counts: Mapping[str, int]
    selected_provider_counts: Mapping[str, int]
    rejection_counts: Mapping[str, int]

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason_code": self.reason_code,
            "summary": self.summary,
            "recommended_action": self.recommended_action,
            "crawl_status": self.crawl_status,
            "crawl_reason_code": self.crawl_reason_code,
            "stage_counts": dict(self.stage_counts),
            "provider_counts": dict(self.provider_counts),
            "selected_provider_counts": dict(self.selected_provider_counts),
            "rejection_counts": dict(self.rejection_counts),
        }


_REASON_DETAILS: dict[str, tuple[str, str, str]] = {
    "completed": (
        "success",
        "خزش و پذیرش محتوای منبع با موفقیت انجام شد.",
        "اقدامی لازم نیست.",
    ),
    "partial_content_success": (
        "partial",
        "بخشی از صفحات به سند قابل استفاده تبدیل شد.",
        "علل رد را بررسی کنید و فقط پرتکرارترین گلوگاه را اصلاح کنید.",
    ),
    "probe_failed": (
        "failed",
        "اتصال اولیه به منبع ناموفق بود.",
        "DNS، TLS، egress و نشانی canonical منبع را بررسی کنید.",
    ),
    "robots_blocked": (
        "blocked",
        "robots.txt خزش این منبع را مسدود کرد.",
        "از RSS یا API رسمی استفاده کنید و محدودیت robots را دور نزنید.",
    ),
    "no_urls_discovered": (
        "empty",
        "هیچ URL خبری از providerهای فعال کشف نشد.",
        "پروفایل منبع، RSS، sitemap و fallback تاریخی را بررسی کنید.",
    ),
    "all_urls_outside_date_range": (
        "empty",
        "تمام URLهای تاریخ‌دار خارج از بازه رصد بودند.",
        "بازه، sitemap تاریخی و تنظیمات discovery را بررسی کنید.",
    ),
    "no_pages_fetched": (
        "empty",
        "URL انتخاب شد اما هیچ صفحه‌ای دریافت نشد.",
        "سقف خزش، admission و مسیر fetch را بررسی کنید.",
    ),
    "all_fetches_failed": (
        "failed",
        "دریافت تمام صفحات انتخاب‌شده ناموفق بود.",
        "کدهای HTTP، anti-bot، browser fallback و egress را بررسی کنید.",
    ),
    "no_topic_match": (
        "empty",
        "صفحات دریافت شدند اما محتوای مرتبط با موضوع نداشتند.",
        "query چندزبانه، امتیازهای رتبه‌بندی و مفاهیم موضوع را بررسی کنید.",
    ),
    "date_out_of_range": (
        "empty",
        "تاریخ انتشار اسناد خارج از بازه رصد بود.",
        "تاریخ ناشر، discovery تاریخی و بازه رصد را بررسی کنید.",
    ),
    "missing_publication_date": (
        "empty",
        "تاریخ انتشار قابل اتکا برای اسناد پیدا نشد.",
        "extractor تاریخ یا پروفایل اختصاصی منبع را تکمیل کنید.",
    ),
    "extraction_failed": (
        "empty",
        "متن مفید و کافی از صفحات استخراج نشد.",
        "ساختار صفحه، browser fallback و extractor را بررسی کنید.",
    ),
    "duplicate_only": (
        "empty",
        "تمام اسناد پذیرفته‌شده تکراری بودند.",
        "منبع سالم است؛ dedupe و هم‌پوشانی URLها را فقط در صورت نیاز بررسی کنید.",
    ),
    "no_documents_prepared": (
        "empty",
        "خزش پایان یافت اما سندی برای تحلیل آماده نشد.",
        "trace مراحل و علت‌های رد پردازش را بررسی کنید.",
    ),
    "unknown": (
        "failed",
        "علت نهایی با داده موجود قابل تشخیص نیست.",
        "ثبت trace و خطاهای worker را بررسی کنید.",
    ),
}

_REJECTION_PRIORITY = (
    "no_topic_match",
    "date_out_of_range",
    "missing_publication_date",
    "extraction_failed",
    "unknown",
)


def _rejection_category(reason: str) -> str:
    normalized = reason.casefold()
    if "relevance score" in normalized:
        return "no_topic_match"
    if "publication date is before" in normalized or "publication date is after" in normalized:
        return "date_out_of_range"
    if "publication date is missing" in normalized or "publication date confidence" in normalized:
        return "missing_publication_date"
    if "too short" in normalized or "extract" in normalized or "crawl was not successful" in normalized:
        return "extraction_failed"
    return "unknown"


class SourceExecutionDiagnoser:
    """Choose one actionable final reason from crawl and processing facts."""

    @classmethod
    def diagnose(cls, facts: SourceExecutionFacts) -> SourceExecutionDiagnostic:
        rejection_counts = Counter(
            _rejection_category(reason) for reason in facts.rejection_reasons
        )
        if facts.prepared_documents:
            reason_code = (
                "completed"
                if not rejection_counts
                and not facts.duplicate_count
                and facts.crawl_reason_code == "completed"
                else "partial_content_success"
            )
        elif facts.crawl_reason_code not in {"completed", "partial_fetch_success"}:
            reason_code = facts.crawl_reason_code
        elif rejection_counts:
            reason_code = min(
                rejection_counts,
                key=lambda code: (
                    -rejection_counts[code],
                    _REJECTION_PRIORITY.index(code),
                ),
            )
        elif facts.duplicate_count:
            reason_code = "duplicate_only"
        else:
            reason_code = "no_documents_prepared"

        status, summary, action = _REASON_DETAILS.get(
            reason_code,
            _REASON_DETAILS["unknown"],
        )
        return SourceExecutionDiagnostic(
            status=status,
            reason_code=reason_code,
            summary=summary,
            recommended_action=action,
            crawl_status=facts.crawl_status,
            crawl_reason_code=facts.crawl_reason_code,
            stage_counts={
                "discovered": facts.discovered_total,
                "selected": facts.selected_total,
                "fetched": facts.fetched_total,
                "fetch_succeeded": facts.successful_pages,
                "prepared": facts.prepared_documents,
                "rejected": len(facts.rejection_reasons),
                "duplicates": facts.duplicate_count,
            },
            provider_counts=dict(facts.provider_counts),
            selected_provider_counts=dict(facts.selected_provider_counts),
            rejection_counts=dict(rejection_counts),
        )
