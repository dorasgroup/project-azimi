"""Application use cases for external cognitive analysis."""

from app.modules.analysis.domain import AnalysisOutcome, AnalysisRequest
from app.modules.analysis.ports import AnalysisGateway


class RunAnalysis:
    def __init__(self, gateway: AnalysisGateway) -> None:
        self._gateway = gateway

    async def execute(self, request: AnalysisRequest) -> AnalysisOutcome:
        return await self._gateway.analyze(request)
