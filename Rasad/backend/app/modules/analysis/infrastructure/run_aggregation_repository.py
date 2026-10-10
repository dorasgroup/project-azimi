"""SQLAlchemy implementation of RunAggregationRepositoryPort."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Mapping
from uuid import UUID, uuid4

from sqlalchemy import select, update, cast, String
from app.models.system_log import SystemLog
from app.models.source_task import SourceTask
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.monitoring_run import MonitoringRun as OrmMonitoringRun
from app.models.completion_tracker import OrmRunCompletionTracker
from app.models.run_aggregation import (
    OrmAggregatedAnalysisResult,
    OrmRunAggregation,
    OrmRunAggregationInput,
)
from app.models.source_analysis import (
    OrmRunAnalysisSpec,
    OrmSourceAnalysisResult,
)
from app.modules.analysis.domain.run_aggregation import (
    AggregatedAnalysisResultData,
    AggregationClaimOutcome,
    AggregationClaimStatus,
    RunAggregation,
    RunAggregationStatus,
    compute_manifest_digest,
)
from app.modules.analysis.ports.run_aggregation import (
    RunAggregationRepositoryPort,
)
from app.shared.integration.outbox import enqueue
from app.shared.integration.consumer_ack import acknowledge_in_session
from app.shared.infrastructure.run_fence import lock_parent_for_child, lock_run


async def _ensure_aggregation_dispatch(session: AsyncSession, orm: OrmRunAggregation) -> None:
    await enqueue(
        session,
        message_key=f"run-aggregate:{orm.run_id}",
        message_type="RUN_AGGREGATION",
        payload={"schema_version": 1, "aggregation_id": str(orm.id)},
    )
    await session.execute(
        update(OrmRunCompletionTracker)
        .where(OrmRunCompletionTracker.run_id == orm.run_id,
               OrmRunCompletionTracker.status.in_(["ready_all_terminal", "ready_deadline"]))
        .values(status="aggregation_dispatched", aggregation_id=orm.id, updated_at=datetime.now(UTC))
    )


def _to_domain(orm: OrmRunAggregation) -> RunAggregation:
    return RunAggregation(
        id=orm.id,
        run_id=orm.run_id,
        status=RunAggregationStatus(orm.status),
        readiness_reason=orm.readiness_reason,
        cutoff_at=orm.cutoff_at,
        manifest_digest=orm.manifest_digest,
        run_analysis_spec_digest=orm.run_analysis_spec_digest,
        attempt_count=orm.attempt_count,
        claimed_at=orm.claimed_at,
        lease_expires_at=orm.lease_expires_at,
        claim_token=orm.claim_token,
        created_at=orm.created_at,
        started_at=orm.started_at,
        finished_at=orm.finished_at,
        updated_at=orm.updated_at,
        error_code=orm.error_code,
        error_message=orm.error_message,
    )


class SqlAlchemyRunAggregationRepository(RunAggregationRepositoryPort):
    """Repository managing RunAggregation lifecycle and input manifests."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def ensure_aggregation_and_manifest(
        self,
        run_id: UUID,
        readiness_reason: str,
        cutoff_at: datetime | None,
        completion_metadata: Mapping[str, object],
        consumed_outbox_id: UUID | None = None,
    ) -> RunAggregation | None:
        async with self._session_factory() as session:
            parent = await lock_run(session, run_id)
            if parent in (None, "cancelled"):
                # Durable stale handoff ACK, with no new manifest/dispatch intent.
                await acknowledge_in_session(session, consumed_outbox_id)
                await session.commit()
                return None
            # 1. Fetch RunAnalysisSpec digest
            spec_res = await session.execute(
                select(OrmRunAnalysisSpec).where(OrmRunAnalysisSpec.run_id == run_id)
            )
            spec_orm = spec_res.scalar_one_or_none()
            spec_digest = spec_orm.spec_digest if spec_orm else "0" * 64

            # 2. Check if RunAggregation already exists
            existing_res = await session.execute(
                select(OrmRunAggregation).where(OrmRunAggregation.run_id == run_id)
            )
            orm = existing_res.scalar_one_or_none()
            if orm is not None:
                await _ensure_aggregation_dispatch(session, orm)
                await acknowledge_in_session(session, consumed_outbox_id)
                await session.commit()
                return _to_domain(orm)

            # 3. Query successful SourceAnalysisResults for run
            q = (
                select(OrmSourceAnalysisResult)
                .where(OrmSourceAnalysisResult.run_id == run_id)
            )
            if cutoff_at is not None:
                q = q.where(OrmSourceAnalysisResult.id.is_not(None))  # in case cutoff filters
            q = q.order_by(
                OrmSourceAnalysisResult.source_id,
                OrmSourceAnalysisResult.id,
            )
            results = (await session.execute(q)).scalars().all()

            # Filter by cutoff_at if applicable
            if cutoff_at is not None:
                # We filter by created_at if cutoff_at was supplied
                results = [r for r in results if not hasattr(r, "created_at") or r.created_at <= cutoff_at]

            # 4. Prepare manifest items and compute digest
            manifest_items: list[dict[str, object]] = []
            for idx, res_item in enumerate(results, start=1):
                manifest_items.append(
                    {
                        "source_analysis_result_id": res_item.id,
                        "source_id": res_item.source_id,
                        "position": idx,
                        "input_digest": res_item.input_digest,
                        "schema_version": res_item.schema_version,
                    }
                )

            completion_counts = {
                "expected_sources": int(completion_metadata.get("expected_sources", len(results))),
                "successful_sources": int(completion_metadata.get("successful_sources", len(results))),
                "failed_sources": int(completion_metadata.get("failed_sources", 0)),
                "cancelled_sources": int(completion_metadata.get("cancelled_sources", 0)),
                "analysis_successful": int(completion_metadata.get("analysis_successful", len(results))),
                "analysis_failed": int(completion_metadata.get("analysis_failed", 0)),
                "analysis_cancelled": int(completion_metadata.get("analysis_cancelled", 0)),
            }
            manifest_digest = compute_manifest_digest(
                inputs=manifest_items,
                readiness_reason=readiness_reason,
                spec_digest=spec_digest,
                cutoff_at=cutoff_at,
                completion_counts=completion_counts,
            )

            aggregation_id = uuid4()
            stmt = (
                pg_insert(OrmRunAggregation)
                .values(
                    id=aggregation_id,
                    run_id=run_id,
                    status=RunAggregationStatus.PENDING.value,
                    readiness_reason=readiness_reason,
                    cutoff_at=cutoff_at,
                    manifest_digest=manifest_digest,
                    run_analysis_spec_digest=spec_digest,
                    attempt_count=0,
                )
                .on_conflict_do_nothing(index_elements=["run_id"])
            )
            await session.execute(stmt)

            # If insert succeeded, populate manifest inputs
            res_agg = await session.execute(
                select(OrmRunAggregation).where(OrmRunAggregation.run_id == run_id)
            )
            orm = res_agg.scalar_one()

            if orm.id == aggregation_id:
                for item in manifest_items:
                    input_stmt = (
                        pg_insert(OrmRunAggregationInput)
                        .values(
                            id=uuid4(),
                            aggregation_id=aggregation_id,
                            source_analysis_result_id=item["source_analysis_result_id"],
                            source_id=item["source_id"],
                            position=item["position"],
                            input_digest=item["input_digest"],
                            schema_version=item["schema_version"],
                        )
                        .on_conflict_do_nothing(
                            constraint="uq_run_aggregation_inputs_agg_result"
                        )
                    )
                    await session.execute(input_stmt)

            await _ensure_aggregation_dispatch(session, orm)
            await acknowledge_in_session(session, consumed_outbox_id)
            await session.commit()
            return _to_domain(orm)

    async def get_by_id(self, aggregation_id: UUID) -> RunAggregation | None:
        async with self._session_factory() as session:
            res = await session.execute(
                select(OrmRunAggregation).where(OrmRunAggregation.id == aggregation_id)
            )
            orm = res.scalar_one_or_none()
            return _to_domain(orm) if orm else None

    async def get_run_coverage(self, run_id: UUID) -> Mapping[str, object]:
        async with self._session_factory() as session:
            run = await session.get(OrmMonitoringRun, run_id)
            if run:
                tracker = await session.scalar(select(OrmRunCompletionTracker).where(OrmRunCompletionTracker.run_id == run_id))
                coverage = {
                    "expected_sources": run.expected_sources,
                    "successful_sources": run.successful_sources,
                    "failed_sources": run.failed_sources,
                    "cancelled_sources": run.cancelled_sources,
                    "analysis_successful": tracker.analysis_successful_count if tracker else 0,
                    "analysis_failed": tracker.analysis_failed_count if tracker else 0,
                    "analysis_cancelled": tracker.analysis_cancelled_count if tracker else 0,
                }
                contexts = (await session.scalars(select(SystemLog.context_json).join(
                    SourceTask, SystemLog.context_json['task_id'].astext == cast(SourceTask.id, String)
                ).where(SourceTask.run_id == run_id, SystemLog.job_id == run.monitoring_job_id,
                        SystemLog.message == 'Source crawled and prepared (distributed)').order_by(SystemLog.created_at))).all()
                by_task = {}
                for context in contexts:
                    diagnostic = context.get('crawl_diagnostics') or {}
                    by_task[context['task_id']] = {'source_name': context.get('source_title'),
                        'document_count': context.get('prepared_documents', 0), 'status': diagnostic.get('status'),
                        'status_message': diagnostic.get('summary'),
                        **{key: diagnostic.get(key, 0) for key in ('selected_total', 'successful_pages', 'failed_pages', 'pending_pages', 'skipped_by_limit')}}
                if by_task:
                    coverage['crawl_sources'] = list(by_task.values())
                    coverage['crawl_fully_fetched_sources'] = sum(item['status'] == 'success' for item in by_task.values())
                    coverage['crawl_partial_sources'] = sum(item['status'] == 'partial' for item in by_task.values())
                    coverage['crawl_unavailable_sources'] = sum(item['status'] not in ('success', 'partial') for item in by_task.values())
                return coverage
            return {}

    async def get_by_run_id(self, run_id: UUID) -> RunAggregation | None:
        async with self._session_factory() as session:
            res = await session.execute(
                select(OrmRunAggregation).where(OrmRunAggregation.run_id == run_id)
            )
            orm = res.scalar_one_or_none()
            return _to_domain(orm) if orm else None

    async def claim_explicit(
        self,
        aggregation_id: UUID,
        claim_token: UUID,
        lease_duration_seconds: int,
        now: datetime,
    ) -> AggregationClaimOutcome:
        async with self._session_factory() as session:
            parent = await lock_parent_for_child(
                session, OrmRunAggregation.run_id, OrmRunAggregation.id, aggregation_id)
            if parent is None:
                return AggregationClaimOutcome(status=AggregationClaimStatus.NOT_FOUND)
            if parent == "cancelled":
                return AggregationClaimOutcome(status=AggregationClaimStatus.RUN_CANCELLED)
            res = await session.execute(
                select(OrmRunAggregation)
                .where(OrmRunAggregation.id == aggregation_id)
                .with_for_update()
            )
            orm = res.scalar_one_or_none()
            if orm is None:
                return AggregationClaimOutcome(status=AggregationClaimStatus.NOT_FOUND)

            status = RunAggregationStatus(orm.status)
            if status.is_terminal:
                return AggregationClaimOutcome(
                    status=AggregationClaimStatus.ALREADY_TERMINAL,
                    current_status=status,
                )

            if status == RunAggregationStatus.RUNNING:
                if orm.lease_expires_at is not None and orm.lease_expires_at > now:
                    return AggregationClaimOutcome(
                        status=AggregationClaimStatus.ACTIVE_LEASE,
                        lease_expires_at=orm.lease_expires_at,
                        attempt_count=orm.attempt_count,
                    )
                # Lease expired: reclaim
                orm.attempt_count += 1
            else:
                # PENDING
                orm.attempt_count += 1

            orm.status = RunAggregationStatus.RUNNING.value
            orm.claim_token = claim_token
            orm.claimed_at = now
            orm.lease_expires_at = now + timedelta(seconds=lease_duration_seconds)
            orm.started_at = orm.started_at or now
            orm.updated_at = now

            await session.commit()
            return AggregationClaimOutcome(
                status=AggregationClaimStatus.CLAIMED,
                claim_token=claim_token,
                lease_expires_at=orm.lease_expires_at,
                attempt_count=orm.attempt_count,
            )

    async def release_claim(
        self,
        aggregation_id: UUID,
        claim_token: UUID,
    ) -> bool:
        async with self._session_factory() as session:
            if await lock_parent_for_child(
                session, OrmRunAggregation.run_id, OrmRunAggregation.id, aggregation_id
            ) in (None, "cancelled"):
                return False
            stmt = (
                update(OrmRunAggregation)
                .where(
                    OrmRunAggregation.id == aggregation_id,
                    OrmRunAggregation.claim_token == claim_token,
                    OrmRunAggregation.status == RunAggregationStatus.RUNNING.value,
                )
                .values(
                    status=RunAggregationStatus.PENDING.value,
                    claim_token=None,
                    claimed_at=None,
                    lease_expires_at=None,
                    updated_at=datetime.now(UTC),
                )
            )
            res = await session.execute(stmt)
            await session.commit()
            return (res.rowcount or 0) > 0

    async def renew_lease(
        self,
        aggregation_id: UUID,
        claim_token: UUID,
        lease_duration_seconds: int,
        now: datetime,
    ) -> bool:
        async with self._session_factory() as session:
            if await lock_parent_for_child(
                session, OrmRunAggregation.run_id, OrmRunAggregation.id, aggregation_id
            ) in (None, "cancelled"):
                return False
            stmt = (
                update(OrmRunAggregation)
                .where(
                    OrmRunAggregation.id == aggregation_id,
                    OrmRunAggregation.claim_token == claim_token,
                    OrmRunAggregation.status == RunAggregationStatus.RUNNING.value,
                )
                .values(
                    lease_expires_at=now + timedelta(seconds=lease_duration_seconds),
                    updated_at=now,
                )
            )
            res = await session.execute(stmt)
            await session.commit()
            return (res.rowcount or 0) > 0

    async def save_result_and_complete(
        self,
        aggregation_id: UUID,
        claim_token: UUID,
        result: AggregatedAnalysisResultData,
        now: datetime,
    ) -> bool:
        async with self._session_factory() as session:
            if await lock_parent_for_child(
                session, OrmRunAggregation.run_id, OrmRunAggregation.id, aggregation_id
            ) in (None, "cancelled"):
                return False
            res = await session.execute(
                select(OrmRunAggregation)
                .where(
                    OrmRunAggregation.id == aggregation_id,
                    OrmRunAggregation.claim_token == claim_token,
                    OrmRunAggregation.status == RunAggregationStatus.RUNNING.value,
                )
                .with_for_update()
            )
            orm = res.scalar_one_or_none()
            if orm is None:
                await session.rollback()
                return False

            # Persist AggregatedAnalysisResult
            result_stmt = (
                pg_insert(OrmAggregatedAnalysisResult)
                .values(
                    id=result.id,
                    aggregation_id=aggregation_id,
                    run_id=orm.run_id,
                    schema_version=result.schema_version,
                    manifest_digest=result.manifest_digest,
                    readiness_reason=result.readiness_reason,
                    expected_sources=result.expected_sources,
                    successful_sources=result.successful_sources,
                    failed_sources=result.failed_sources,
                    cancelled_sources=result.cancelled_sources,
                    analysis_successful=result.analysis_successful,
                    analysis_failed=result.analysis_failed,
                    analysis_cancelled=result.analysis_cancelled,
                    summary=result.summary,
                    findings=result.findings,
                    coverage_metadata=result.coverage_metadata,
                    provenance_references=result.provenance_references,
                    raw_result=result.raw_result,
                    cutoff_at=result.cutoff_at,
                    manifest_schema_version=result.manifest_schema_version,
                    created_at=now,
                )
                .on_conflict_do_nothing(index_elements=["aggregation_id"])
            )
            await session.execute(result_stmt)

            # Mark completed
            orm.status = RunAggregationStatus.COMPLETED.value
            orm.finished_at = now
            orm.updated_at = now
            orm.claim_token = None
            orm.lease_expires_at = None

            await enqueue(
                session,
                message_key=f"analysis-ready:{aggregation_id}",
                message_type="ANALYSIS_READY",
                payload={
                    "schema_version": 1, "event": "analysis_ready",
                    "run_id": str(orm.run_id), "aggregation_id": str(aggregation_id),
                    "result_id": str(result.id),
                },
            )
            await session.commit()
            return True

    async def mark_failed(
        self,
        aggregation_id: UUID,
        claim_token: UUID,
        error_code: str,
        error_message: str,
        now: datetime,
    ) -> bool:
        async with self._session_factory() as session:
            if await lock_parent_for_child(
                session, OrmRunAggregation.run_id, OrmRunAggregation.id, aggregation_id
            ) in (None, "cancelled"):
                return False
            stmt = (
                update(OrmRunAggregation)
                .where(
                    OrmRunAggregation.id == aggregation_id,
                    OrmRunAggregation.claim_token == claim_token,
                    OrmRunAggregation.status == RunAggregationStatus.RUNNING.value,
                )
                .values(
                    status=RunAggregationStatus.FAILED.value,
                    error_code=error_code,
                    error_message=error_message,
                    finished_at=now,
                    updated_at=now,
                    claim_token=None,
                    lease_expires_at=None,
                )
            )
            res = await session.execute(stmt)
            if (res.rowcount or 0) > 0:
                await enqueue(
                    session, message_key=f"aggregation-terminal:{aggregation_id}",
                    message_type="AGGREGATION_TERMINAL",
                    payload={"schema_version": 1, "aggregation_id": str(aggregation_id)},
                )
            await session.commit()
            return (res.rowcount or 0) > 0

    async def load_manifest_inputs(
        self,
        aggregation_id: UUID,
    ) -> list[dict[str, object]]:
        async with self._session_factory() as session:
            q = (
                select(
                    OrmRunAggregationInput,
                    OrmSourceAnalysisResult,
                )
                .join(
                    OrmSourceAnalysisResult,
                    OrmRunAggregationInput.source_analysis_result_id == OrmSourceAnalysisResult.id,
                )
                .where(OrmRunAggregationInput.aggregation_id == aggregation_id)
                .order_by(OrmRunAggregationInput.position)
            )
            rows = (await session.execute(q)).all()
            items: list[dict[str, object]] = []
            for inp, res_item in rows:
                items.append(
                    {
                        "source_analysis_result_id": inp.source_analysis_result_id,
                        "source_id": inp.source_id,
                        "position": inp.position,
                        "input_digest": inp.input_digest,
                        "schema_version": inp.schema_version,
                        "summary": res_item.summary,
                        "findings": res_item.findings,
                        "raw_result": res_item.raw_result,
                        "evidence_map": res_item.evidence_map if hasattr(res_item, "evidence_map") else {},
                    }
                )
            return items
