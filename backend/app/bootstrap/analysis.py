"""Composition root for the Analysis and Reports modules."""

from sqlalchemy.ext.asyncio import AsyncSession
from typing import Mapping

from app.modules.analysis.infrastructure.operations import AnalysisJobCoordinator
from app.modules.analysis.infrastructure import (
    NativeAnalysisGateway,
    OpenAICompatibleModelGateway,
    SqlAlchemyAnalysisJobReader,
)
from app.modules.analysis.ports import AnalysisGateway, ModelGateway
from app.modules.reports.infrastructure import (
    ReportExportBatchAdapter,
    SqlAlchemyReportRepository,
)


def provide_model_gateway() -> OpenAICompatibleModelGateway:
    """Build the project-owned model gateway."""

    return OpenAICompatibleModelGateway()


def provide_frozen_model_gateway(spec: Mapping[str, object]) -> OpenAICompatibleModelGateway:
    from app.modules.platform_settings.infrastructure.provider_runtime import get_pinned_model_provider
    provider = get_pinned_model_provider(str(spec["model_provider"]), str(spec["model_name"]))
    return OpenAICompatibleModelGateway(provider)


def provide_analysis_gateway(
    *,
    model_gateway: ModelGateway | None = None,
) -> AnalysisGateway:
    return NativeAnalysisGateway(model_gateway or provide_model_gateway())


def provide_analysis_job_coordinator(
    session: AsyncSession,
    gateway: AnalysisGateway | None = None,
) -> AnalysisJobCoordinator:
    return AnalysisJobCoordinator(
        session,
        gateway or provide_analysis_gateway(),
        ReportExportBatchAdapter(),
        SqlAlchemyReportRepository(session),
        SqlAlchemyAnalysisJobReader(session),
    )
