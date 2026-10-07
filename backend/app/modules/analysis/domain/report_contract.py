"""Provider-neutral structured report contract for cognitive analysis."""

from collections.abc import Mapping

from pydantic import AnyHttpUrl, BaseModel, Field, JsonValue, field_validator, model_validator


class ExecutiveSummaryMetric(BaseModel):
    value: str
    label: str

    @field_validator("value", mode="before")
    @classmethod
    def normalize_numeric_value(cls, value: object) -> object:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
        return value


class ExecutiveSummary(BaseModel):
    text: str
    metrics: list[ExecutiveSummaryMetric] = Field(default_factory=list)


class TimelineEvent(BaseModel):
    date: str
    description: str


class SourceArticle(BaseModel):
    date: str
    title: str
    summary: str
    url: AnyHttpUrl

    @model_validator(mode="before")
    @classmethod
    def normalize_article_aliases(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        normalized = dict(value)
        aliases = {
            "date": ("published_at", "published_date", "publication_date"),
            "title": ("headline", "name"),
            "summary": ("description", "content", "analysis"),
            "url": ("link", "source_url"),
        }
        for target, candidates in aliases.items():
            if normalized.get(target):
                continue
            replacement = next(
                (normalized.get(candidate) for candidate in candidates if normalized.get(candidate)),
                None,
            )
            if replacement is not None:
                normalized[target] = replacement
        normalized.setdefault("date", "نامشخص")
        normalized.setdefault("title", "بدون عنوان")
        normalized.setdefault("summary", "")
        return normalized


class SourceGroup(BaseModel):
    source_name: str
    articles: list[SourceArticle] = Field(default_factory=list)


class CognitiveAnalysisSubSection(BaseModel):
    title: str
    content: str


class CognitiveAnalysis(BaseModel):
    sub_sections: list[CognitiveAnalysisSubSection] = Field(default_factory=list)
    key_findings: list[str] = Field(default_factory=list)

    @field_validator("key_findings", mode="before")
    @classmethod
    def normalize_structured_findings(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        normalized: list[object] = []
        for item in value:
            if not isinstance(item, Mapping):
                normalized.append(item)
                continue
            text = next(
                (
                    item.get(key)
                    for key in ("finding", "text", "content", "description")
                    if item.get(key)
                ),
                None,
            )
            name = item.get("name") or item.get("title")
            if name and text and str(name).strip() not in str(text):
                normalized.append(f"{name}: {text}")
            elif text:
                normalized.append(str(text))
            elif name:
                normalized.append(str(name))
            else:
                normalized.append(str(dict(item)))
        return normalized


class Scenario(BaseModel):
    title: str
    probability: str
    description: str
    implications: str


class FutureVariable(BaseModel):
    name: str
    description: str


class FutureAnalysis(BaseModel):
    scenarios: list[Scenario] = Field(default_factory=list)
    key_variables: list[FutureVariable] = Field(default_factory=list)


class ReportSource(BaseModel):
    title: str
    url: AnyHttpUrl
    source_name: str


class AnalysisReportDocument(BaseModel):
    """Stable report document shared by every current and future analysis engine."""

    title: str
    executive_summary: ExecutiveSummary
    timeline: list[TimelineEvent] = Field(default_factory=list)
    sources_analysis: list[SourceGroup] = Field(default_factory=list)
    cognitive_analysis: CognitiveAnalysis
    future_analysis: FutureAnalysis
    conclusion: str
    sources: list[ReportSource] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_optional_tail_sections(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        normalized = dict(value)
        normalized.setdefault("future_analysis", {"scenarios": [], "key_variables": []})
        if not normalized.get("conclusion"):
            summary = normalized.get("executive_summary")
            if isinstance(summary, Mapping) and summary.get("text"):
                normalized["conclusion"] = str(summary["text"])
            else:
                normalized["conclusion"] = "جمع‌بندی مستقلی در خروجی مدل ارائه نشد."
        return normalized

    @field_validator("sources_analysis", mode="before")
    @classmethod
    def normalize_source_group_mapping(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        groups: list[dict[str, object]] = []
        for source_name, group in value.items():
            if isinstance(group, Mapping):
                normalized = dict(group)
                normalized.setdefault("source_name", str(source_name))
                normalized.setdefault("articles", [])
            elif isinstance(group, list):
                nested_groups = [
                    dict(item)
                    for item in group
                    if isinstance(item, Mapping) and isinstance(item.get("articles"), list)
                ]
                if nested_groups:
                    for nested in nested_groups:
                        nested.setdefault("source_name", str(source_name))
                        nested.setdefault("articles", [])
                    groups.extend(nested_groups)
                    continue
                normalized = {"source_name": str(source_name), "articles": group}
            else:
                normalized = {"source_name": str(source_name), "articles": []}
            groups.append(normalized)
        return groups

    def as_content(self) -> dict[str, JsonValue]:
        return self.model_dump(mode="json")
