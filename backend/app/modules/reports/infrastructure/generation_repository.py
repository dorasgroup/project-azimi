"""Transactional persistence for distributed report-generation requests and leases."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from app.shared.infrastructure.run_fence import lock_parent_for_child

from app.models.completion_tracker import OrmRunCompletionTracker
from app.models.analysis_report import AnalysisReport
from app.models.monitoring_job import MonitoringJob
from app.models.monitoring_run import MonitoringRun
from app.models.enums import JobStatus
from app.models.report_generation import OrmReportArtifact, OrmReportGeneration
from app.models.run_aggregation import OrmAggregatedAnalysisResult
from app.modules.monitoring.domain.execution import MonitoringRunStatus
from app.modules.reports.domain.generation import (
    ReportClaim, ReportClaimKind, ReportGenerationInput, ReportGenerationSpec, ReportGenerationStatus,
    REPORT_SPEC_VERSION, REPORT_SCHEMA_VERSION, REPORT_TEMPLATE_VERSION,
    REQUIRED_FORMATS, report_spec_digest,
)
from app.shared.integration.consumer_ack import acknowledge_in_session
from app.shared.integration.outbox import enqueue


class SqlAlchemyReportGenerationRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def ensure_from_analysis_ready(
        self, run_id: UUID, result_id: UUID, consumed_outbox_id: UUID | None,
    ) -> UUID | None:
        """Atomically ensure one frozen generation, successor intent and handoff ACK."""
        async with self._sessions() as session:
            run = await session.scalar(
                select(MonitoringRun).where(MonitoringRun.id == run_id).with_for_update()
            )
            if run is None:
                raise ValueError("Report handoff references a missing run")
            if run.execution_mode != "distributed":
                raise ValueError("Report handoff must not target a legacy run")
            existing = await session.scalar(select(OrmReportGeneration).where(
                OrmReportGeneration.run_id == run_id,
                OrmReportGeneration.aggregated_analysis_result_id == result_id,
            ))
            if existing is not None:
                # Its first transaction already persisted the successor obligation.
                # Never rebuild a spec from mutable job data on redelivery, including
                # redelivery after Monitoring has finalized the run.
                await acknowledge_in_session(session, consumed_outbox_id)
                await session.commit()
                return existing.id
            job = await session.get(MonitoringJob, run.monitoring_job_id, with_for_update=True)
            if run.status == MonitoringRunStatus.CANCELLED or (job is not None and job.status == JobStatus.CANCELLED):
                if consumed_outbox_id is not None:
                    await acknowledge_in_session(session, consumed_outbox_id)
                await session.commit()
                return None
            if run.status != MonitoringRunStatus.RUNNING:
                raise ValueError("Report handoff run is not active")

            result = await session.get(OrmAggregatedAnalysisResult, result_id)
            if result is None or result.run_id != run_id:
                raise ValueError("Report handoff aggregate does not match run")
            tracker = await session.scalar(
                select(OrmRunCompletionTracker)
                .where(OrmRunCompletionTracker.run_id == run_id)
            )
            if tracker is None or tracker.status != "analysis_ready" or tracker.aggregation_id != result.aggregation_id:
                raise ValueError("Report handoff lacks accepted ANALYSIS_READY state")
            job = await session.get(MonitoringJob, run.monitoring_job_id, with_for_update=True)
            if job is None:
                raise ValueError("Report handoff references missing monitoring job")
            spec_json = {
                "title": job.topic, "created_by": str(job.requested_by),
                "date_from_jalali": job.date_from_jalali,
                "date_to_jalali": job.date_to_jalali,
                "locale": "fa-IR", "formats": list(REQUIRED_FORMATS),
                "spec_version": REPORT_SPEC_VERSION,
                "schema_version": REPORT_SCHEMA_VERSION,
                "template_version": REPORT_TEMPLATE_VERSION,
            }
            invalid_spec = False
            try:
                ReportGenerationSpec.from_json(spec_json)
            except (ValueError, TypeError, KeyError):
                invalid_spec = True

            await session.execute(
                pg_insert(OrmReportGeneration)
                .values(
                    id=uuid4(), run_id=run_id,
                    aggregated_analysis_result_id=result_id,
                    status="failed" if invalid_spec else "pending",
                    spec_json=spec_json, spec_digest=report_spec_digest(spec_json),
                    manifest_digest=result.manifest_digest,
                    attempt_count=0,
                    failed_at=datetime.now(UTC) if invalid_spec else None,
                    error_code="InvalidImmutableReportSpec" if invalid_spec else None,
                    sanitized_error="Report generation failed." if invalid_spec else None,
                )
                .on_conflict_do_nothing(constraint="uq_report_generations_run_aggregate")
            )
            generation = await session.scalar(
                select(OrmReportGeneration).where(
                    OrmReportGeneration.run_id == run_id,
                    OrmReportGeneration.aggregated_analysis_result_id == result_id,
                )
            )
            if generation is None:
                raise RuntimeError("Report generation ensure did not create or find row")
            if invalid_spec:
                await self._enqueue_terminal(session, generation.id)
            else:
                await enqueue(
                    session,
                    message_key=f"report-generation:{generation.id}",
                    message_type="REPORT_GENERATION",
                    payload={"schema_version": 1, "report_generation_id": str(generation.id)},
                )
            if consumed_outbox_id is not None:
                await acknowledge_in_session(session, consumed_outbox_id)
            await session.commit()
            return generation.id

    async def claim(
        self, generation_id: UUID, token: UUID, lease_seconds: int, now: datetime,
    ) -> ReportClaim:
        if lease_seconds <= 0 or now.tzinfo is None:
            raise ValueError("Report lease must be positive and timezone-aware")
        async with self._sessions() as session:
            await lock_parent_for_child(session, OrmReportGeneration.run_id, OrmReportGeneration.id, generation_id)
            generation = await session.scalar(
                select(OrmReportGeneration)
                .where(OrmReportGeneration.id == generation_id)
                .with_for_update()
            )
            if generation is None:
                return ReportClaim(ReportClaimKind.NOT_FOUND)
            if generation.status in {
                ReportGenerationStatus.COMPLETED.value,
                ReportGenerationStatus.FAILED.value,
                ReportGenerationStatus.CANCELLED.value,
            }:
                return ReportClaim(ReportClaimKind.ALREADY_TERMINAL)
            run = await session.get(MonitoringRun, generation.run_id)
            if run is None:
                raise ValueError("Report generation references missing run")
            job = await session.get(MonitoringJob, run.monitoring_job_id, with_for_update=True)
            if run.status == MonitoringRunStatus.CANCELLED or (job is not None and job.status == JobStatus.CANCELLED):
                generation.status = ReportGenerationStatus.CANCELLED.value
                generation.cancelled_at = now
                generation.claim_token = None
                generation.lease_expires_at = None
                await self._enqueue_terminal(session, generation.id)
                await session.commit()
                return ReportClaim(ReportClaimKind.RUN_CANCELLED)
            if run.status != MonitoringRunStatus.RUNNING:
                raise ValueError("Report generation run is not active")
            if (
                generation.status == ReportGenerationStatus.RUNNING.value
                and generation.lease_expires_at is not None
                and generation.lease_expires_at > now
            ):
                return ReportClaim(
                    ReportClaimKind.ACTIVE_LEASE,
                    lease_expires_at=generation.lease_expires_at,
                    attempt_count=generation.attempt_count,
                )
            generation.status = ReportGenerationStatus.RUNNING.value
            generation.claim_token = token
            generation.claimed_at = now
            generation.lease_expires_at = now + timedelta(seconds=lease_seconds)
            generation.started_at = generation.started_at or now
            generation.attempt_count += 1
            generation.updated_at = now
            await session.commit()
            return ReportClaim(
                ReportClaimKind.CLAIMED, token=token,
                lease_expires_at=generation.lease_expires_at,
                attempt_count=generation.attempt_count,
            )

    async def renew_lease(
        self, generation_id: UUID, token: UUID, lease_seconds: int, now: datetime,
    ) -> bool:
        async with self._sessions() as session:
            changed = await session.execute(
                update(OrmReportGeneration)
                .where(
                    OrmReportGeneration.id == generation_id,
                    OrmReportGeneration.claim_token == token,
                    OrmReportGeneration.status == ReportGenerationStatus.RUNNING.value,
                    OrmReportGeneration.lease_expires_at > now,
                    OrmReportGeneration.run_id.in_(select(MonitoringRun.id).join(MonitoringJob, MonitoringJob.id == MonitoringRun.monitoring_job_id).where(MonitoringRun.status == MonitoringRunStatus.RUNNING, MonitoringJob.status != JobStatus.CANCELLED)),
                )
                .values(
                    lease_expires_at=now + timedelta(seconds=lease_seconds),
                    updated_at=now,
                )
            )
            await session.commit()
            return (changed.rowcount or 0) == 1

    async def release_for_retry(self, generation_id: UUID, token: UUID) -> bool:
        async with self._sessions() as session:
            changed = await session.execute(
                update(OrmReportGeneration).where(
                    OrmReportGeneration.id == generation_id,
                    OrmReportGeneration.claim_token == token,
                    OrmReportGeneration.status == "running",
                ).values(status="pending", claim_token=None, lease_expires_at=None, updated_at=datetime.now(UTC))
            )
            await session.commit()
            return (changed.rowcount or 0) == 1

    async def load_input(self, generation_id: UUID, token: UUID) -> ReportGenerationInput | None:
        """A report renderer receives no source/runtime tables or current settings."""
        now = datetime.now(UTC)
        async with self._sessions() as session:
            generation = await session.scalar(
                select(OrmReportGeneration).where(
                    OrmReportGeneration.id == generation_id,
                    OrmReportGeneration.status == ReportGenerationStatus.RUNNING.value,
                    OrmReportGeneration.claim_token == token,
                    OrmReportGeneration.lease_expires_at > now,
                )
            )
            if generation is None:
                return None
            result = await session.get(
                OrmAggregatedAnalysisResult, generation.aggregated_analysis_result_id,
            )
            if (
                result is None or result.run_id != generation.run_id
                or result.manifest_digest != generation.manifest_digest
            ):
                raise ValueError("Pinned aggregate provenance is invalid")
            spec = ReportGenerationSpec.from_json(generation.spec_json)
            if spec.digest != generation.spec_digest:
                raise ValueError("Frozen report spec digest does not match")
            return ReportGenerationInput(
                generation_id=generation.id, run_id=generation.run_id,
                aggregated_result_id=result.id,
                manifest_digest=result.manifest_digest,
                aggregate_schema_version=result.schema_version,
                manifest_schema_version=result.manifest_schema_version,
                readiness_reason=result.readiness_reason,
                cutoff_at=result.cutoff_at,
                expected_sources=result.expected_sources,
                successful_sources=result.successful_sources,
                failed_sources=result.failed_sources,
                cancelled_sources=result.cancelled_sources,
                analysis_successful=result.analysis_successful,
                analysis_failed=result.analysis_failed,
                analysis_cancelled=result.analysis_cancelled,
                summary=result.summary,
                findings=tuple(deepcopy(result.findings)),
                coverage_metadata=deepcopy(result.coverage_metadata),
                provenance_references=tuple(deepcopy(result.provenance_references)),
                spec=spec,
                report_snapshot=deepcopy((result.raw_result or {}).get("report_snapshot")),
            )

    async def artifact_metadata(
        self, generation_id: UUID, token: UUID,
    ) -> dict[str, tuple[str, str, int]]:
        """Read accepted artifacts only while this worker owns a live claim."""
        async with self._sessions() as session:
            generation = await session.get(OrmReportGeneration, generation_id)
            if not self._owns_live_claim(generation, token, datetime.now(UTC)):
                return {}
            rows = (await session.scalars(
                select(OrmReportArtifact).where(
                    OrmReportArtifact.report_generation_id == generation_id,
                )
            )).all()
            return {
                row.format: (row.storage_key, row.content_checksum, row.content_size)
                for row in rows
            }

    async def accept_artifact(
        self, generation_id: UUID, token: UUID, *, export_format: str,
        storage_key: str, checksum: str, size: int,
        schema_version: str, template_version: str,
    ) -> bool:
        """Fence metadata acceptance after a content-addressed file has been written."""
        now = datetime.now(UTC)
        async with self._sessions() as session:
            await lock_parent_for_child(session, OrmReportGeneration.run_id, OrmReportGeneration.id, generation_id)
            generation = await session.scalar(
                select(OrmReportGeneration)
                .where(OrmReportGeneration.id == generation_id).with_for_update()
            )
            if not self._owns_live_claim(generation, token, now):
                return False
            run = await session.scalar(
                select(MonitoringRun).where(MonitoringRun.id == generation.run_id).with_for_update()
            )
            if run is None or run.status != MonitoringRunStatus.RUNNING:
                return False
            job = await session.get(MonitoringJob, run.monitoring_job_id, with_for_update=True)
            if job is None or job.status == JobStatus.CANCELLED:
                return False
            spec = ReportGenerationSpec.from_json(generation.spec_json)
            if (
                export_format not in spec.formats
                or schema_version != spec.schema_version
                or template_version != spec.template_version
                or storage_key != f"{generation_id}-{checksum}.{export_format}"
                or size < 1
            ):
                raise ValueError("Artifact metadata does not match frozen report spec")
            await session.execute(
                pg_insert(OrmReportArtifact)
                .values(
                    id=uuid4(), report_generation_id=generation_id,
                    format=export_format, storage_key=storage_key,
                    content_checksum=checksum, content_size=size,
                    report_schema_version=schema_version,
                    template_version=template_version,
                )
                .on_conflict_do_update(
                    constraint="uq_report_artifacts_generation_format",
                    set_={
                        "storage_key": storage_key, "content_checksum": checksum,
                        "content_size": size, "report_schema_version": schema_version,
                        "template_version": template_version,
                    },
                )
            )
            await session.commit()
            return True

    async def complete(
        self, generation_id: UUID, token: UUID,
        artifact_paths: dict[str, str],
        *, full_report: dict[str, object], sources: list[dict[str, object]],
    ) -> bool:
        """One fenced transaction accepts the required set, public row and terminal intent."""
        now = datetime.now(UTC)
        async with self._sessions() as session:
            await lock_parent_for_child(session, OrmReportGeneration.run_id, OrmReportGeneration.id, generation_id)
            generation = await session.scalar(
                select(OrmReportGeneration)
                .where(OrmReportGeneration.id == generation_id).with_for_update()
            )
            if not self._owns_live_claim(generation, token, now):
                return False
            run = await session.scalar(
                select(MonitoringRun).where(MonitoringRun.id == generation.run_id).with_for_update()
            )
            if run is None or run.status != MonitoringRunStatus.RUNNING:
                return False
            job = await session.get(MonitoringJob, run.monitoring_job_id, with_for_update=True)
            if job is None or job.status == JobStatus.CANCELLED:
                return False
            spec = ReportGenerationSpec.from_json(generation.spec_json)
            rows = (await session.scalars(
                select(OrmReportArtifact).where(
                    OrmReportArtifact.report_generation_id == generation_id,
                )
            )).all()
            artifacts = {row.format: row for row in rows}
            if set(artifacts) != set(spec.formats):
                return False
            if set(artifact_paths) != set(spec.formats) or any(
                not artifact_paths[fmt].endswith("/" + row.storage_key)
                for fmt, row in artifacts.items()
            ):
                return False
            report = await session.scalar(
                select(AnalysisReport)
                .where(AnalysisReport.job_id == run.monitoring_job_id)
                .with_for_update()
            )
            if report is None:
                summary = full_report.get("executive_summary")
                if not isinstance(summary, dict) or not isinstance(summary.get("text"), str):
                    raise ValueError("Report document lacks executive summary")
                report = AnalysisReport(
                    id=uuid4(), job_id=run.monitoring_job_id,
                    title=spec.title, summary=summary["text"],
                    full_report=full_report, sources_json=sources,
                    created_by=spec.created_by,
                )
                session.add(report)
            else:
                report.title = spec.title
                report.summary = str(full_report["executive_summary"]["text"])
                report.full_report = full_report
                report.sources_json = sources
            report.pdf_path = artifact_paths["pdf"]
            report.docx_path = artifact_paths["docx"]
            generation.status = ReportGenerationStatus.COMPLETED.value
            generation.completed_at = now
            generation.claim_token = None
            generation.lease_expires_at = None
            generation.updated_at = now
            await self._enqueue_terminal(session, generation_id)
            await session.commit()
            return True

    async def fail_permanently(
        self, generation_id: UUID, token: UUID, error_code: str,
    ) -> bool:
        """Do not store renderer exception text: it may contain document content."""
        now = datetime.now(UTC)
        if not error_code or len(error_code) > 64 or not error_code.isascii():
            raise ValueError("Invalid sanitized report failure code")
        async with self._sessions() as session:
            await lock_parent_for_child(session, OrmReportGeneration.run_id, OrmReportGeneration.id, generation_id)
            generation = await session.scalar(
                select(OrmReportGeneration)
                .where(OrmReportGeneration.id == generation_id).with_for_update()
            )
            if not self._owns_live_claim(generation, token, now):
                return False
            run = await session.scalar(
                select(MonitoringRun).where(MonitoringRun.id == generation.run_id).with_for_update()
            )
            job = await session.get(MonitoringJob, run.monitoring_job_id, with_for_update=True) if run is not None else None
            generation.status = (
                ReportGenerationStatus.CANCELLED.value
                if run is not None and (run.status == MonitoringRunStatus.CANCELLED or (job is not None and job.status == JobStatus.CANCELLED))
                else ReportGenerationStatus.FAILED.value
            )
            if generation.status == ReportGenerationStatus.CANCELLED.value:
                generation.cancelled_at = now
            else:
                generation.failed_at = now
                generation.error_code = error_code
                generation.sanitized_error = "Report generation failed."
            generation.claim_token = None
            generation.lease_expires_at = None
            generation.updated_at = now
            await self._enqueue_terminal(session, generation_id)
            await session.commit()
            return True

    @staticmethod
    def _owns_live_claim(
        generation: OrmReportGeneration | None, token: UUID, now: datetime,
    ) -> bool:
        return bool(
            generation is not None
            and generation.status == ReportGenerationStatus.RUNNING.value
            and generation.claim_token == token
            and generation.lease_expires_at is not None
            and generation.lease_expires_at > now
        )

    @staticmethod
    async def _enqueue_terminal(session: AsyncSession, generation_id: UUID) -> None:
        await enqueue(
            session,
            message_key=f"report-terminal:{generation_id}",
            message_type="REPORT_TERMINAL",
            payload={"schema_version": 1, "report_generation_id": str(generation_id)},
        )
