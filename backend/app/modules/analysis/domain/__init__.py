"""Analysis input and outcome types."""

from app.modules.analysis.domain.contracts import (
    AnalysisExecutiveSummary,
    AnalysisOutcome,
    AnalysisReportPayload,
    AnalysisRequest,
    AnalysisSourceReference,
)
from app.modules.analysis.domain.model_contract import (
    ModelGenerationRequest,
    ModelGenerationResult,
)
from app.modules.analysis.domain.report_contract import AnalysisReportDocument
from app.modules.analysis.domain.run_aggregation import (
    AggregatedAnalysisResultData,
    AggregationClaimOutcome,
    AggregationClaimStatus,
    RunAggregation,
    RunAggregationInputItem,
    RunAggregationStatus,
    compute_manifest_digest,
)
from app.modules.analysis.domain.run_analysis_spec import (
    RunAnalysisSpec,
    compute_run_spec_digest,
    validate_run_spec_secrets,
)
from app.modules.analysis.domain.source_analysis import (
    SOURCE_ANALYSIS_HEARTBEAT_SECONDS,
    SOURCE_ANALYSIS_LEASE_SECONDS,
    SOURCE_ANALYSIS_MAX_ATTEMPTS,
    AnalysisClaimOutcome,
    AnalysisClaimStatus,
    AnalysisError,
    PermanentAnalysisError,
    SourceAnalysis,
    SourceAnalysisEvidence,
    SourceAnalysisResultData,
    SourceAnalysisSpec,
    SourceAnalysisStatus,
    TransientAnalysisError,
    classify_analysis_error,
    compute_analysis_input_digest,
)

__all__ = [
    "AggregatedAnalysisResultData",
    "AggregationClaimOutcome",
    "AggregationClaimStatus",
    "AnalysisExecutiveSummary",
    "AnalysisOutcome",
    "AnalysisReportPayload",
    "AnalysisReportDocument",
    "AnalysisRequest",
    "AnalysisSourceReference",
    "ModelGenerationRequest",
    "ModelGenerationResult",
    "RunAggregation",
    "RunAggregationInputItem",
    "RunAggregationStatus",
    "RunAnalysisSpec",
    "compute_manifest_digest",
    "compute_run_spec_digest",
    "validate_run_spec_secrets",
    "SOURCE_ANALYSIS_HEARTBEAT_SECONDS",
    "SOURCE_ANALYSIS_LEASE_SECONDS",
    "SOURCE_ANALYSIS_MAX_ATTEMPTS",
    "AnalysisClaimOutcome",
    "AnalysisClaimStatus",
    "AnalysisError",
    "PermanentAnalysisError",
    "SourceAnalysis",
    "SourceAnalysisEvidence",
    "SourceAnalysisResultData",
    "SourceAnalysisSpec",
    "SourceAnalysisStatus",
    "TransientAnalysisError",
    "classify_analysis_error",
    "compute_analysis_input_digest",
]
