import os

from scripts.test_environment import assert_safe_test_environment

# Reject unsafe targets before importing application composition or fixtures.
assert_safe_test_environment(os.environ)

import asyncio
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import engine
from app.models import (
    AnalysisReport,
    BotIdentity,
    CrawledDocument,
    MonitoringJob,
    RefreshToken,
    Source,
    SourceType,
    SystemLog,
    User,
    UserRole,
)
from app.models.enums import LogLevel


async def expect_integrity_error(session: AsyncSession, instance: object) -> None:
    try:
        async with session.begin_nested():
            session.add(instance)
            await session.flush()
    except IntegrityError:
        return
    raise AssertionError(f"Expected an integrity error for {type(instance).__name__}")


async def verify_database() -> None:
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)

        try:
            counted_models = (
                User,
                Source,
                BotIdentity,
                MonitoringJob,
                CrawledDocument,
                AnalysisReport,
                RefreshToken,
                SystemLog,
            )
            initial_counts = {
                model.__tablename__: await session.scalar(select(func.count()).select_from(model))
                or 0
                for model in counted_models
            }
            default_identity_exists = bool(
                await session.scalar(select(BotIdentity.id).where(BotIdentity.is_default.is_(True)))
            )
            admin = User(
                full_name="Database Verification Admin",
                username="db-verification-admin",
                email="db-verification@example.test",
                password_hash="not-a-real-password-hash",
                role=UserRole.SUPER_ADMIN,
            )
            identity = BotIdentity(
                title="Default verification identity",
                role_definition="Verification analyst",
                is_default=not default_identity_exists,
                creator=admin,
            )
            source = Source(
                title="Verification source",
                base_url="https://example.test/news",
                type=SourceType.NEWS_SITE,
                creator=admin,
            )
            job = MonitoringJob(
                topic="Database verification",
                date_from_jalali="1405/03/01",
                date_to_jalali="1405/03/02",
                date_from_gregorian=date(2026, 5, 22),
                date_to_gregorian=date(2026, 5, 23),
                requester=admin,
                bot_identity=identity,
                sources=[source],
            )
            document = CrawledDocument(
                job=job,
                source=source,
                url="https://example.test/news/1",
                title="Verification document",
                clean_text="Verification content",
                matched_score=0.8,
            )
            report = AnalysisReport(
                job=job,
                title="Verification report",
                summary="Verification summary",
                full_report={"status": "verified"},
                sources_json=[{"url": document.url}],
                creator=admin,
            )
            token = RefreshToken(
                user=admin,
                token_hash="verification-token-hash",
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
            log = SystemLog(
                level=LogLevel.INFO,
                module="database_verification",
                message="Database verification completed",
            )

            session.add_all([admin, identity, source, job, document, report, token, log])
            await session.flush()

            counts = {
                model.__tablename__: await session.scalar(select(func.count()).select_from(model))
                for model in counted_models
            }
            assert all(count == initial_counts[table] + 1 for table, count in counts.items()), (
                counts
            )
            assert len(job.sources) == 1
            assert job.report is report

            await expect_integrity_error(
                session,
                BotIdentity(
                    title="Second default identity",
                    role_definition="Must be rejected",
                    is_default=True,
                    created_by=admin.id,
                ),
            )
            await expect_integrity_error(
                session,
                MonitoringJob(
                    topic="Invalid date range",
                    date_from_jalali="1405/03/02",
                    date_to_jalali="1405/03/01",
                    date_from_gregorian=date(2026, 5, 23),
                    date_to_gregorian=date(2026, 5, 22),
                    requested_by=admin.id,
                    bot_identity_id=identity.id,
                ),
            )
            await expect_integrity_error(
                session,
                CrawledDocument(
                    job_id=job.id,
                    source_id=source.id,
                    url="https://example.test/news/invalid-score",
                    matched_score=1.5,
                ),
            )

            print("database verification passed")
        finally:
            await session.close()
            await transaction.rollback()
            await engine.dispose()


if __name__ == "__main__":
    asyncio.run(verify_database())
