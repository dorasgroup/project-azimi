from enum import StrEnum

from app.modules.identity.domain.roles import UserRole
from app.shared.domain.logging import LogLevel


class SourceType(StrEnum):
    NEWS_SITE = "news_site"
    RSS = "rss"
    CUSTOM_PAGE = "custom_page"


class SourceStatus(StrEnum):
    UNKNOWN = "unknown"
    SUCCESS = "success"
    FAILED = "failed"


class SourceCrawlStatus(StrEnum):
    UNKNOWN = "unknown"
    SUCCESS = "success"
    PARTIAL = "partial"
    EMPTY = "empty"
    BLOCKED = "blocked"
    FAILED = "failed"


class JobStatus(StrEnum):
    PENDING = "pending"
    CRAWLING = "crawling"
    PROCESSING = "processing"
    ANALYZING = "analyzing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
