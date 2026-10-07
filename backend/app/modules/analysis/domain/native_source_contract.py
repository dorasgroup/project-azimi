"""Versioned cognitive evidence from a single source; no runtime/provider logic."""

from pydantic import BaseModel, ConfigDict, Field
from app.modules.analysis.domain.report_contract import CognitiveAnalysis, FutureAnalysis


class NativeSourceFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=500)
    explanation: str = Field(min_length=1, max_length=8000)
    evidence: str = Field(min_length=1, max_length=4000)
    document_url: str = Field(min_length=1, max_length=4000)
    topic: str = ""
    sentiment: str = ""
    trend: str = ""
    contradiction: str = ""


class NativeSourceDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1, max_length=12000)
    findings: list[NativeSourceFinding] = Field(max_length=50)
    evidence_map: dict[str, str]
    cognitive_analysis: CognitiveAnalysis
    future_analysis: FutureAnalysis
    coverage_notes: list[str] = Field(default_factory=list)

