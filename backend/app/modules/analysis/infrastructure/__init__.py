"""Analysis infrastructure adapters."""

from app.modules.analysis.infrastructure.model_gateway import OpenAICompatibleModelGateway
from app.modules.analysis.infrastructure.native_gateway import NativeAnalysisGateway

__all__ = [
    "NativeAnalysisGateway",
    "OpenAICompatibleModelGateway",
    "SqlAlchemyAnalysisJobReader",
]


def __getattr__(name: str):
    if name == "SqlAlchemyAnalysisJobReader":
        from app.modules.analysis.infrastructure.job_reader import SqlAlchemyAnalysisJobReader

        return SqlAlchemyAnalysisJobReader
    raise AttributeError(name)
