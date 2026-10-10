"""Value objects for explainable hybrid candidate ranking."""

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class HybridRankingWeights:
    lexical: float = 0.35
    semantic: float = 0.30
    evidence: float = 0.20
    temporal: float = 0.10
    structure: float = 0.05


@dataclass(frozen=True)
class CandidateHybridScore:
    lexical: float
    semantic: float
    evidence: float
    temporal: float
    structure: float
    total: float

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass
class RankingTrace:
    enabled: bool
    topic_preserved: bool = True
    candidate_count: int = 0
    eligible_count: int = 0
    selected_count: int = 0
    weights: dict[str, float] = field(default_factory=dict)
    top_candidates: list[dict[str, object]] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return asdict(self)
