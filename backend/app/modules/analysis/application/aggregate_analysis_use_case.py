"""Application use case for executing RunAggregation."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import logging
from uuid import UUID, uuid4

from app.modules.analysis.domain.run_aggregation import (
    AGGREGATION_SCHEMA_VERSION,
    MANIFEST_SCHEMA_VERSION,
    AggregatedAnalysisResultData,
    AggregationClaimStatus,
)
from app.modules.analysis.ports.run_aggregation import (
    MonitoringAnalysisReadyPublisherPort,
    RunAggregationRepositoryPort,
    RunAggregatorPort,
)
from app.modules.analysis.ports.run_analysis_spec import (
    RunAnalysisSpecRepositoryPort,
)
from app.modules.analysis.domain.source_analysis import NoRelevantEvidenceError, SourceAnalysisUnavailableError, PermanentAnalysisError

logger = logging.getLogger("rasad.analysis.aggregate")

RUN_AGGREGATION_LEASE_SECONDS = 300
RUN_AGGREGATION_HEARTBEAT_SECONDS = 30
RUN_AGGREGATION_MAX_ATTEMPTS = 3


class ExecuteRunAggregationUseCase:
    """Executes RunAggregation: claims execution, runs heartbeat, loads manifest inputs, produces aggregated result, and publishes analysis_ready."""

    def __init__(
        self,
        aggregation_repository: RunAggregationRepositoryPort,
        run_spec_repository: RunAnalysisSpecRepositoryPort,
        aggregator: RunAggregatorPort,
        ready_publisher: MonitoringAnalysisReadyPublisherPort | None,
        lease_duration_seconds: int = RUN_AGGREGATION_LEASE_SECONDS,
        heartbeat_interval_seconds: int = RUN_AGGREGATION_HEARTBEAT_SECONDS,
    ) -> None:
        self._aggregation_repo = aggregation_repository
        self._run_spec_repo = run_spec_repository
        self._aggregator = aggregator
        self._ready_publisher = ready_publisher
        self._lease_duration_seconds = lease_duration_seconds
        self._heartbeat_interval_seconds = heartbeat_interval_seconds

    async def execute(self, aggregation_id: UUID) -> dict[str, object]:
        """Keep failed attempts fenced and the terminal notification durable."""
        return await self._execute(aggregation_id)

    async def _execute(self, aggregation_id: UUID) -> dict[str, object]:
        now = datetime.now(UTC)
        claim_token = uuid4()

        claim = await self._aggregation_repo.claim_explicit(
            aggregation_id=aggregation_id,
            claim_token=claim_token,
            lease_duration_seconds=self._lease_duration_seconds,
            now=now,
        )

        if claim.status == AggregationClaimStatus.NOT_FOUND:
            return {"status": "not_found", "aggregation_id": str(aggregation_id)}

        if claim.status == AggregationClaimStatus.RUN_CANCELLED:
            return {"status": "run_cancelled", "aggregation_id": str(aggregation_id)}

        if claim.status == AggregationClaimStatus.ALREADY_TERMINAL:
            # Crash recovery guarantee: if already completed, ensure analysis_ready signal was published
            agg = await self._aggregation_repo.get_by_id(aggregation_id)
            if agg and agg.status.value == "completed" and self._ready_publisher is not None:
                # Ensure ready event is republished in case worker crashed between commit and publish
                self._ready_publisher.publish_analysis_ready(
                    run_id=agg.run_id,
                    aggregation_id=agg.id,
                    result_id=agg.id,  # references aggregation
                )
            return {
                "status": "already_terminal",
                "aggregation_id": str(aggregation_id),
                "terminal_status": claim.current_status.value if claim.current_status else "completed",
            }

        if claim.status == AggregationClaimStatus.ACTIVE_LEASE:
            return {
                "status": "active_lease",
                "aggregation_id": str(aggregation_id),
                "lease_expires_at": claim.lease_expires_at.isoformat() if claim.lease_expires_at else None,
            }

        # Status is CLAIMED
        agg = await self._aggregation_repo.get_by_id(aggregation_id)
        if agg is None:
            return {"status": "not_found", "aggregation_id": str(aggregation_id)}

        try:
            return await self._execute_claimed(agg, claim_token)
        except Exception as exc:
            current = await self._aggregation_repo.get_by_id(aggregation_id)
            if current is not None and current.status.value == "completed":
                # Legacy injected publisher may fail after the atomic result /
                # successor commit. Keep success immutable and retry delivery.
                raise
            # Never persist provider content/credentials in errors or the outbox.
            logger.warning("Aggregation attempt failed (%s)", type(exc).__name__)
            permanent = isinstance(exc, (PermanentAnalysisError, ValueError))
            if permanent or agg.attempt_count >= RUN_AGGREGATION_MAX_ATTEMPTS:
                no_evidence = isinstance(exc, NoRelevantEvidenceError)
                source_failed = isinstance(exc, SourceAnalysisUnavailableError)
                won = await self._aggregation_repo.mark_failed(
                    aggregation_id=agg.id, claim_token=claim_token,
                    error_code="SOURCE_ANALYSIS_FAILED" if source_failed else ("NO_RELEVANT_EVIDENCE" if no_evidence else ("AGGREGATION_INVALID" if permanent else "AGGREGATION_RETRY_EXHAUSTED")),
                    error_message="No accepted source evidence exists for synthesis" if no_evidence else "Native aggregation could not produce a valid report",
                    now=datetime.now(UTC),
                )
                return {"status": "failed" if won else "stale_fenced", "aggregation_id": str(agg.id)}
            released = await self._aggregation_repo.release_claim(agg.id, claim_token)
            return {"status": "retry_transient" if released else "stale_fenced", "aggregation_id": str(agg.id)}

    async def _execute_claimed(self, agg, claim_token: UUID) -> dict[str, object]:
        aggregation_id = agg.id
        now = datetime.now(UTC)
        # Load frozen RunAnalysisSpec
        run_spec = await self._run_spec_repo.get_by_run_id(agg.run_id)
        spec_dict = run_spec.spec if run_spec else {}

        # Load manifest inputs
        inputs = await self._aggregation_repo.load_manifest_inputs(aggregation_id)

        run_coverage = await self._aggregation_repo.get_run_coverage(agg.run_id)
        expected_sources = int(run_coverage.get("expected_sources", len(inputs)))
        successful_sources = int(run_coverage.get("successful_sources", len(inputs)))
        failed_sources = int(run_coverage.get("failed_sources", 0))
        cancelled_sources = int(run_coverage.get("cancelled_sources", 0))
        analysis_successful = int(run_coverage.get("analysis_successful", len(inputs)))
        analysis_failed = int(run_coverage.get("analysis_failed", 0))
        analysis_cancelled = int(run_coverage.get("analysis_cancelled", 0))

        coverage_metadata = {
            **{key: value for key, value in run_coverage.items() if key.startswith('crawl_')},
            "readiness_reason": agg.readiness_reason,
            "cutoff_at": agg.cutoff_at.isoformat() if agg.cutoff_at else None,
            "manifest_input_count": len(inputs),
            "expected_sources": expected_sources,
            "successful_sources": successful_sources,
            "failed_sources": failed_sources,
            "cancelled_sources": cancelled_sources,
            "analysis_successful": analysis_successful,
            "analysis_failed": analysis_failed,
            "analysis_cancelled": analysis_cancelled,
        }

        # Background heartbeat loop during aggregation execution
        stop_heartbeat = asyncio.Event()
        heartbeat_failed = False

        async def _heartbeat_worker() -> None:
            nonlocal heartbeat_failed
            while not stop_heartbeat.is_set():
                try:
                    await asyncio.wait_for(stop_heartbeat.wait(), timeout=self._heartbeat_interval_seconds)
                    break
                except asyncio.TimeoutError:
                    renewed = await self._aggregation_repo.renew_lease(
                        aggregation_id=agg.id,
                        claim_token=claim_token,
                        lease_duration_seconds=self._lease_duration_seconds,
                        now=datetime.now(UTC),
                    )
                    if not renewed:
                        logger.warning("Heartbeat failed for %s: claim lost or expired", agg.id)
                        heartbeat_failed = True
                        break

        heartbeat_task = asyncio.create_task(_heartbeat_worker())

        try:
            agg_result = await self._aggregator.aggregate(
                spec=spec_dict,
                inputs=inputs,
                coverage_metadata=coverage_metadata,
            )
        finally:
            stop_heartbeat.set()
            await heartbeat_task

        if heartbeat_failed:
            logger.warning("Heartbeat lost during aggregation for %s; aborting persistence", aggregation_id)
            return {"status": "stale_fenced", "aggregation_id": str(aggregation_id)}

        expected_sources = int(coverage_metadata.get("expected_sources", len(inputs)))
        successful_sources = int(coverage_metadata.get("successful_sources", len(inputs)))
        failed_sources = int(coverage_metadata.get("failed_sources", 0))
        cancelled_sources = int(coverage_metadata.get("cancelled_sources", 0))
        analysis_successful = int(coverage_metadata.get("analysis_successful", len(inputs)))
        analysis_failed = int(coverage_metadata.get("analysis_failed", 0))
        analysis_cancelled = int(coverage_metadata.get("analysis_cancelled", 0))

        full_coverage_metadata = {
            **{key: value for key, value in run_coverage.items() if key.startswith('crawl_')},
            "expected_sources": expected_sources,
            "successful_sources": successful_sources,
            "failed_sources": failed_sources,
            "cancelled_sources": cancelled_sources,
            "analysis_successful": analysis_successful,
            "analysis_failed": analysis_failed,
            "analysis_cancelled": analysis_cancelled,
            "readiness_reason": agg.readiness_reason,
            "cutoff_at": agg.cutoff_at.isoformat() if agg.cutoff_at else None,
            "manifest_input_count": len(inputs),
        }

        try:
            result_id = uuid4()
            result_data = AggregatedAnalysisResultData(
                id=result_id,
                aggregation_id=agg.id,
                run_id=agg.run_id,
                schema_version=AGGREGATION_SCHEMA_VERSION,
                manifest_digest=agg.manifest_digest,
                readiness_reason=agg.readiness_reason,
                expected_sources=expected_sources,
                successful_sources=successful_sources,
                failed_sources=failed_sources,
                cancelled_sources=cancelled_sources,
                analysis_successful=analysis_successful,
                analysis_failed=analysis_failed,
                analysis_cancelled=analysis_cancelled,
                summary=str(agg_result.get("summary", "")),
                findings=list(agg_result.get("findings", [])),  # type: ignore
                coverage_metadata=full_coverage_metadata,
                provenance_references=list(agg_result.get("provenance_references", [])),  # type: ignore
                raw_result=agg_result.get("raw_result"),  # type: ignore
                created_at=now,
                cutoff_at=agg.cutoff_at,
                manifest_schema_version=MANIFEST_SCHEMA_VERSION,
            )

            saved = await self._aggregation_repo.save_result_and_complete(
                aggregation_id=agg.id,
                claim_token=claim_token,
                result=result_data,
                now=datetime.now(UTC),
            )

            if not saved:
                logger.warning("Fenced aggregation save for %s (claim token expired or mismatched)", aggregation_id)
                return {"status": "stale_fenced", "aggregation_id": str(aggregation_id)}

            # Publish integration event
            if self._ready_publisher is not None:
                self._ready_publisher.publish_analysis_ready(
                    run_id=agg.run_id,
                    aggregation_id=agg.id,
                    result_id=result_id,
                )

            return {
                "status": "completed",
                "aggregation_id": str(agg.id),
                "result_id": str(result_id),
            }

        except Exception:
            # Outer boundary performs fenced release/terminalization. A failed
            # DB/outbox commit must not acknowledge the incoming obligation.
            raise
