"""Compose the Analysis-owned immutable spec freezer at the module boundary."""

from app.modules.analysis.infrastructure.handoff_adapter import AnalysisHandoffAdapter
from app.modules.analysis.infrastructure.spec_resolver import SourceAnalysisSpecResolver


def provide_distributed_analysis_freezer(sessions):
    return AnalysisHandoffAdapter(
        sessions,
        spec_resolver=SourceAnalysisSpecResolver(require_configured=True, default_prompt_version="native-source-v2"),
        freeze_job_context=True,
    )
