import os

from scripts.test_environment import assert_safe_test_environment

# Reject unsafe targets before importing application composition or fixtures.
assert_safe_test_environment(os.environ)

import asyncio
from datetime import date
from pathlib import Path
from zipfile import ZipFile

from sqlalchemy.ext.asyncio import AsyncSession

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
from app.modules.reports.infrastructure.operations import ReportCommandAdapter


REPORT_DATA = {
    "title": "گزارش آزمون خروجی",
    "executive_summary": "این خلاصه برای بررسی خروجی PDF و Word فارسی است.",
    "monitoring_scope": "رصد آزمایشی منابع خبری",
    "key_findings": ["یافته اول", "یافته دوم"],
    "cognitive_analysis": "متن تحلیل شناختی با محتوای راست‌به‌چپ و فارسی.",
    "narratives": [{"title": "روایت اول", "description": "شرح روایت اول"}],
    "actors": [{"name": "بازیگر نمونه", "role": "موضوع خبر", "portrayal": "بازنمایی خنثی"}],
    "media_patterns": ["تکرار کلیدواژه‌ها"],
    "psychological_operations_signals": ["نشانه احتمالی و غیرقطعی"],
    "conclusion": "جمع‌بندی گزارش آزمایشی.",
    "research_suggestions": ["بررسی منبع دوم"],
}


async def verify_report_exports() -> None:
    generated_paths: list[Path] = []
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            user = User(
                full_name="Report Export Verification",
                username="report-export-verification",
                email="report-export@example.test",
                password_hash="not-a-real-password-hash",
                role=UserRole.SUPER_ADMIN,
            )
            identity = BotIdentity(
                title="تحلیلگر گزارش",
                role_definition="ساخت خروجی گزارش",
                creator=user,
            )
            source = Source(
                title="منبع آزمون",
                base_url="https://example.com/export",
                type=SourceType.NEWS_SITE,
                creator=user,
            )
            job = MonitoringJob(
                topic="خروجی گزارش",
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
                        "title": "منبع آزمون",
                        "url": "https://example.com/export/news/1",
                        "source_name": "منبع آزمون",
                    }
                ],
            )
            session.add_all([user, identity, source, job, report])
            await session.flush()

            pdf_path, docx_path = ReportExportGenerator().generate_all(report)
            report.pdf_path = pdf_path
            report.docx_path = docx_path
            await session.flush()
            generated_paths.extend([Path(pdf_path), Path(docx_path)])

            assert Path(pdf_path).read_bytes().startswith(b"%PDF")
            assert ZipFile(docx_path).testzip() is None

            Path(pdf_path).unlink()
            Path(docx_path).unlink()

            lazy_pdf = await ReportCommandAdapter(session).export_path(report.id, "pdf")
            lazy_docx = await ReportCommandAdapter(session).export_path(report.id, "docx")
            assert lazy_pdf and lazy_pdf.read_bytes().startswith(b"%PDF")
            assert lazy_docx and ZipFile(lazy_docx).testzip() is None
            generated_paths.extend([lazy_pdf, lazy_docx])

            print(f"Report export verification passed: report={report.id}")
        finally:
            for path in generated_paths:
                if path.exists():
                    path.unlink()
            await session.close()
            await transaction.rollback()
            await engine.dispose()


if __name__ == "__main__":
    asyncio.run(verify_report_exports())
