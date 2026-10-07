"""Reporting-owned integration read view; terminal lifecycle is immutable."""

from uuid import UUID
from sqlalchemy import select

from app.models.report_generation import OrmReportArtifact, OrmReportGeneration
from app.models.run_aggregation import OrmAggregatedAnalysisResult
from app.modules.reports.domain.generation import ReportGenerationSpec
from app.shared.integration.report_terminal import ReportTerminalSnapshot


class ReportTerminalReader:
    def __init__(self, sessions) -> None:
        self._sessions = sessions

    async def read(self, generation_id: UUID) -> ReportTerminalSnapshot:
        async with self._sessions() as session:
            generation = await session.get(OrmReportGeneration, generation_id)
            if generation is None or generation.status not in {"completed", "failed", "cancelled"}:
                raise ValueError("Report terminal event lacks durable terminal generation")
            result = await session.get(OrmAggregatedAnalysisResult, generation.aggregated_analysis_result_id)
            if result is None or result.run_id != generation.run_id or result.manifest_digest != generation.manifest_digest:
                raise ValueError("Report terminal provenance is invalid")
            durable = False
            if generation.status == "completed":
                spec = ReportGenerationSpec.from_json(generation.spec_json)
                artifacts = (await session.scalars(select(OrmReportArtifact).where(OrmReportArtifact.report_generation_id == generation_id))).all()
                durable = spec.digest == generation.spec_digest and {a.format for a in artifacts} == set(spec.formats) and all(
                    a.content_size > 0 and len(a.content_checksum) == 64
                    and a.report_schema_version == spec.schema_version
                    and a.template_version == spec.template_version
                    for a in artifacts
                )
            return ReportTerminalSnapshot(generation_id, generation.run_id, result.aggregation_id, generation.status, durable)
