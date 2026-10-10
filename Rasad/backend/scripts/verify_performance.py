import os

from scripts.test_environment import assert_safe_test_environment

# Reject unsafe targets before importing application composition or fixtures.
assert_safe_test_environment(os.environ)

import asyncio
import time
from datetime import date
from pathlib import Path

from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.crawler.engine import CrawlEngine
from app.db.session import engine
from app.models import (
    AnalysisReport,
    BotIdentity,
    MonitoringJob,
    Source,
    SourceType,
    User,
    UserRole,
)
from app.reports import ReportExportGenerator


REPORT_DATA = {
    "title": "گزارش سنجش سرعت",
    "executive_summary": "خلاصه کوتاه برای سنجش سرعت تولید خروجی.",
    "monitoring_scope": "سنجش داخلی",
    "key_findings": ["یافته اول", "یافته دوم", "یافته سوم"],
    "cognitive_analysis": "تحلیل نمونه برای تولید خروجی‌های گزارش.",
    "narratives": [{"title": "روایت", "description": "شرح روایت"}],
    "actors": [{"name": "بازیگر", "role": "نقش", "portrayal": "بازنمایی"}],
    "media_patterns": ["الگوی اول", "الگوی دوم"],
    "psychological_operations_signals": ["نشانه احتمالی"],
    "conclusion": "جمع‌بندی",
    "research_suggestions": ["پیشنهاد"],
}


async def verify_robots_cache() -> None:
    crawler = CrawlEngine()
    calls = 0

    async def fake_safe_get(url: str, *, max_redirects: int = 5) -> Response:
        nonlocal calls
        calls += 1
        return Response(200, text="User-agent: *\nAllow: /\n")

    crawler._safe_get = fake_safe_get  # type: ignore[method-assign]
    assert await crawler.robots_allowed("https://example.com/news/1")
    assert await crawler.robots_allowed("https://example.com/news/2")
    assert calls == 1, f"robots.txt should be fetched once per origin, got {calls}"


async def verify_report_export_speed() -> None:
    generated_paths: list[Path] = []
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            user = User(
                full_name="Performance Verification",
                username="performance-verification",
                email="performance@example.test",
                password_hash="not-a-real-password-hash",
                role=UserRole.SUPER_ADMIN,
            )
            identity = BotIdentity(
                title="تحلیلگر سرعت",
                role_definition="سنجش سرعت گزارش",
                creator=user,
            )
            source = Source(
                title="منبع سرعت",
                base_url="https://example.com/performance",
                type=SourceType.NEWS_SITE,
                creator=user,
            )
            job = MonitoringJob(
                topic="سنجش سرعت",
                date_from_jalali="1405/03/01",
                date_to_jalali="1405/03/17",
                date_from_gregorian=date(2026, 5, 22),
                date_to_gregorian=date(2026, 6, 7),
                requester=user,
                bot_identity=identity,
                sources=[source],
            )
            report = AnalysisReport(
                job=job,
                creator=user,
                title=REPORT_DATA["title"],
                summary=REPORT_DATA["executive_summary"],
                full_report=REPORT_DATA,
                sources_json=[
                    {
                        "title": "منبع سرعت",
                        "url": "https://example.com/performance/1",
                        "source_name": "منبع سرعت",
                    }
                ],
            )
            session.add_all([user, identity, source, job, report])
            await session.flush()

            generator = ReportExportGenerator()
            started = time.perf_counter()
            for _ in range(3):
                pdf_path, docx_path = generator.generate_all(report)
                generated_paths.extend([Path(pdf_path), Path(docx_path)])
            elapsed = time.perf_counter() - started
            assert elapsed < 15, f"Report exports are unexpectedly slow: {elapsed:.2f}s"
            print(
                f"Performance verification passed: robots_cached=true, report_exports_3x={elapsed:.2f}s"
            )
        finally:
            for path in generated_paths:
                if path.exists():
                    path.unlink()
            await session.close()
            await transaction.rollback()
            await engine.dispose()


async def verify() -> None:
    await verify_robots_cache()
    await verify_report_export_speed()


if __name__ == "__main__":
    asyncio.run(verify())
