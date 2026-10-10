from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator

from app.crawler.schemas import CrawlPolicy
from app.models.enums import SourceCrawlStatus, SourceStatus, SourceType
from app.modules.crawling.domain.source_configuration import (
    normalize_source_discovery_profile,
)
from app.processing.schemas import ProcessingPolicy


class SourceCrawlConfiguration(BaseModel):
    """Validated, forward-safe configuration stored in the existing JSON column."""

    model_config = ConfigDict(extra="forbid")

    crawl_policy: CrawlPolicy | None = None
    processing_policy: ProcessingPolicy | None = None

    def storage_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True, exclude_unset=True)


class SourceCreate(BaseModel):
    title: str = Field(min_length=1, max_length=250)
    base_url: AnyHttpUrl
    type: SourceType
    is_active: bool = True
    crawl_config: SourceCrawlConfiguration = Field(
        default_factory=SourceCrawlConfiguration
    )

    @model_validator(mode="after")
    def normalize_discovery_profile(self) -> "SourceCreate":
        policy = self.crawl_config.crawl_policy
        if policy is None or "discovery_profile" not in policy.model_fields_set:
            return self
        profile = normalize_source_discovery_profile(
            str(self.base_url),
            policy.discovery_profile,
        )
        normalized_policy = policy.model_copy(update={"discovery_profile": profile})
        self.crawl_config = self.crawl_config.model_copy(
            update={"crawl_policy": normalized_policy}
        )
        return self


class SourceUpdate(SourceCreate):
    pass


class SourceRead(BaseModel):
    id: UUID
    title: str
    base_url: str
    type: SourceType
    is_active: bool
    crawl_config: dict[str, Any] = Field(default_factory=dict)
    last_status: SourceStatus
    last_status_message: str | None
    last_checked_at: datetime | None
    last_crawl_status: SourceCrawlStatus
    last_crawl_message: str | None
    last_crawled_at: datetime | None


class SourceCheckResponse(BaseModel):
    status: SourceStatus
    http_status: int | None
    response_time_ms: int | None
    reason: str | None
    last_checked_at: datetime
