"""SQLAlchemy repository for per-source cognitive analysis lifecycle."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.source_analysis import OrmSourceAnalysis
from app.modules.analysis.domain.source_analysis import (
    SOURCE_ANALYSIS_LEASE_SECONDS,
    AnalysisClaimOutcome,
    AnalysisClaimStatus,
    SourceAnalysis,
    SourceAnalysisSpec,
    SourceAnalysisStatus,
)
from app.shared.integration.outbox import enqueue
from app.shared.infrastructure.run_fence import lock_parent_for_child

logger = logging.getLogger("rasad.analysis.repository")


class SqlAlchemySourceAnalysisRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _parent_status(self, analysis_id: UUID) -> str | None:
        return await lock_parent_for_child(
            self._session, OrmSourceAnalysis.run_id, OrmSourceAnalysis.id, analysis_id)

    async def _rejected_claim(self, outcome: AnalysisClaimOutcome) -> AnalysisClaimOutcome:
        # Claim owns its transaction. A no-op must not retain a parent lock
        # while a duplicate delivery waits for an active owner's work.
        await self._session.commit()
        return outcome

    async def get_by_id(self, analysis_id: UUID) -> SourceAnalysis | None:
        row = (
            await self._session.execute(
                select(OrmSourceAnalysis).where(OrmSourceAnalysis.id == analysis_id)
            )
        ).scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def get_by_source_task_id(self, task_id: UUID) -> SourceAnalysis | None:
        row = (
            await self._session.execute(
                select(OrmSourceAnalysis).where(OrmSourceAnalysis.source_task_id == task_id)
            )
        ).scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def ensure_for_source_task(
        self,
        source_task_id: UUID,
        run_id: UUID,
        source_id: UUID,
        spec: SourceAnalysisSpec,
    ) -> SourceAnalysis:
        """Idempotently ensure SourceAnalysis row exists with status PENDING."""
        stmt = (
            insert(OrmSourceAnalysis)
            .values(
                source_task_id=source_task_id,
                run_id=run_id,
                source_id=source_id,
                status=SourceAnalysisStatus.PENDING,
                analysis_spec=spec.to_dict(),
                attempt_count=0,
            )
            .on_conflict_do_nothing(index_elements=["source_task_id"])
            .returning(OrmSourceAnalysis)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            # Already existed, fetch existing
            row = (
                await self._session.execute(
                    select(OrmSourceAnalysis).where(OrmSourceAnalysis.source_task_id == source_task_id)
                )
            ).scalar_one()
        await self._session.flush()
        return self._to_domain(row)

    async def claim_explicit(
        self,
        analysis_id: UUID,
        worker_token: UUID | None = None,
        lease_seconds: int = SOURCE_ANALYSIS_LEASE_SECONDS,
    ) -> AnalysisClaimOutcome:
        """Atomically claim a SourceAnalysis using row-level locking."""
        parent = await self._parent_status(analysis_id)
        if parent is None:
            return await self._rejected_claim(AnalysisClaimOutcome(status=AnalysisClaimStatus.NOT_FOUND))
        if parent == "cancelled":
            return await self._rejected_claim(AnalysisClaimOutcome(status=AnalysisClaimStatus.RUN_CANCELLED))
        now = datetime.now(UTC)
        token = worker_token or uuid4()
        expires = now + timedelta(seconds=lease_seconds)

        stmt = select(OrmSourceAnalysis).where(OrmSourceAnalysis.id == analysis_id).with_for_update()
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return await self._rejected_claim(AnalysisClaimOutcome(status=AnalysisClaimStatus.NOT_FOUND))

        if row.status in (SourceAnalysisStatus.COMPLETED, SourceAnalysisStatus.FAILED, SourceAnalysisStatus.CANCELLED):
            return await self._rejected_claim(AnalysisClaimOutcome(
                status=AnalysisClaimStatus.ALREADY_TERMINAL,
                current_status=row.status,
                attempt_count=row.attempt_count,
            ))

        if row.status == SourceAnalysisStatus.RUNNING:
            if row.lease_expires_at is not None and row.lease_expires_at > now:
                return await self._rejected_claim(AnalysisClaimOutcome(
                    status=AnalysisClaimStatus.ACTIVE_LEASE,
                    current_status=row.status,
                    lease_expires_at=row.lease_expires_at,
                    attempt_count=row.attempt_count,
                ))

        # Claim or reclaim
        row.status = SourceAnalysisStatus.RUNNING
        row.claim_token = token
        row.claimed_at = now
        row.lease_expires_at = expires
        row.attempt_count += 1
        if row.started_at is None:
            row.started_at = now
        row.updated_at = now

        # Commit immediately so concurrent workers see running status and token
        await self._session.commit()
        return AnalysisClaimOutcome(
            status=AnalysisClaimStatus.CLAIMED,
            claim_token=token,
            lease_expires_at=expires,
            attempt_count=row.attempt_count,
            current_status=SourceAnalysisStatus.RUNNING,
        )

    async def renew_lease(
        self,
        analysis_id: UUID,
        claim_token: UUID,
        extension_seconds: int = SOURCE_ANALYSIS_LEASE_SECONDS,
    ) -> bool:
        """Atomically extend lease for a running SourceAnalysis if token matches."""
        if await self._parent_status(analysis_id) in (None, "cancelled"):
            await self._session.rollback()
            return False
        now = datetime.now(UTC)
        new_expires = now + timedelta(seconds=extension_seconds)

        stmt = (
            update(OrmSourceAnalysis)
            .where(
                OrmSourceAnalysis.id == analysis_id,
                OrmSourceAnalysis.status == SourceAnalysisStatus.RUNNING,
                OrmSourceAnalysis.claim_token == claim_token,
                OrmSourceAnalysis.lease_expires_at >= now,
            )
            .values(
                lease_expires_at=new_expires,
                updated_at=now,
            )
            .returning(OrmSourceAnalysis.id)
        )
        res = await self._session.execute(stmt)
        matched = res.scalar_one_or_none() is not None
        if matched:
            await self._session.commit()
        else:
            await self._session.rollback()
        return matched

    async def release_claim(
        self,
        analysis_id: UUID,
        claim_token: UUID,
    ) -> bool:
        """Release claim on transient failure: transition RUNNING -> PENDING, clear token and lease."""
        if await self._parent_status(analysis_id) in (None, "cancelled"):
            await self._session.rollback()
            return False
        now = datetime.now(UTC)
        stmt = (
            update(OrmSourceAnalysis)
            .where(
                OrmSourceAnalysis.id == analysis_id,
                OrmSourceAnalysis.status == SourceAnalysisStatus.RUNNING,
                OrmSourceAnalysis.claim_token == claim_token,
            )
            .values(
                status=SourceAnalysisStatus.PENDING,
                claim_token=None,
                lease_expires_at=None,
                updated_at=now,
            )
            .returning(OrmSourceAnalysis.id)
        )
        res = await self._session.execute(stmt)
        matched = res.scalar_one_or_none() is not None
        if matched:
            await self._session.commit()
        else:
            await self._session.rollback()
        return matched

    async def complete_analysis(
        self,
        analysis_id: UUID,
        claim_token: UUID,
        input_digest: str | None = None,
    ) -> bool:
        """Atomically transition SourceAnalysis to COMPLETED if token matches."""
        if await self._parent_status(analysis_id) in (None, "cancelled"):
            await self._session.rollback()
            return False
        now = datetime.now(UTC)
        values: dict = {
            "status": SourceAnalysisStatus.COMPLETED,
            "finished_at": now,
            "updated_at": now,
            "claim_token": None,
            "lease_expires_at": None,
        }
        if input_digest:
            values["input_digest"] = input_digest

        stmt = (
            update(OrmSourceAnalysis)
            .where(
                OrmSourceAnalysis.id == analysis_id,
                OrmSourceAnalysis.status == SourceAnalysisStatus.RUNNING,
                OrmSourceAnalysis.claim_token == claim_token,
            )
            .values(**values)
            .returning(OrmSourceAnalysis.id)
        )
        res = await self._session.execute(stmt)
        matched = res.scalar_one_or_none() is not None
        if matched:
            row = await self._session.get(OrmSourceAnalysis, analysis_id)
            assert row is not None
            await enqueue(
                self._session,
                message_key=f"analysis-terminal:{analysis_id}",
                message_type="ANALYSIS_TERMINAL",
                payload={
                    "schema_version": 1, "event": "analysis_terminal",
                    "run_id": str(row.run_id), "source_analysis_id": str(analysis_id),
                    "outcome": "completed",
                },
            )
            await self._session.commit()
        else:
            await self._session.rollback()
        return matched

    async def fail_analysis(
        self,
        analysis_id: UUID,
        claim_token: UUID,
        error_code: str,
        error_message: str,
    ) -> bool:
        """Atomically transition SourceAnalysis to FAILED if token matches."""
        if await self._parent_status(analysis_id) in (None, "cancelled"):
            await self._session.rollback()
            return False
        now = datetime.now(UTC)
        stmt = (
            update(OrmSourceAnalysis)
            .where(
                OrmSourceAnalysis.id == analysis_id,
                OrmSourceAnalysis.status == SourceAnalysisStatus.RUNNING,
                OrmSourceAnalysis.claim_token == claim_token,
            )
            .values(
                status=SourceAnalysisStatus.FAILED,
                claim_token=None,
                lease_expires_at=None,
                error_code=error_code,
                error_message=error_message,
                finished_at=now,
                updated_at=now,
            )
            .returning(OrmSourceAnalysis.id)
        )
        res = await self._session.execute(stmt)
        matched = res.scalar_one_or_none() is not None
        if matched:
            row = await self._session.get(OrmSourceAnalysis, analysis_id)
            assert row is not None
            await enqueue(
                self._session,
                message_key=f"analysis-terminal:{analysis_id}",
                message_type="ANALYSIS_TERMINAL",
                payload={
                    "schema_version": 1, "event": "analysis_terminal",
                    "run_id": str(row.run_id), "source_analysis_id": str(analysis_id),
                    "outcome": "failed",
                },
            )
            await self._session.commit()
        else:
            await self._session.rollback()
        return matched

    @staticmethod
    def _to_domain(row: OrmSourceAnalysis) -> SourceAnalysis:
        return SourceAnalysis(
            id=row.id,
            source_task_id=row.source_task_id,
            run_id=row.run_id,
            source_id=row.source_id,
            status=row.status,
            attempt_count=row.attempt_count,
            claimed_at=row.claimed_at,
            lease_expires_at=row.lease_expires_at,
            claim_token=row.claim_token,
            input_digest=row.input_digest,
            analysis_spec=SourceAnalysisSpec.from_dict(row.analysis_spec),
            error_code=row.error_code,
            error_message=row.error_message,
            created_at=row.created_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
            updated_at=row.updated_at,
        )
