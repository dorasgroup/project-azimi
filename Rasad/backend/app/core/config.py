from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    project_name: str = "Rasad Analysis"
    environment: str = "development"
    database_url: str = "postgresql+asyncpg://rasad:change-me@postgres:5432/rasad"
    redis_url: str = "redis://rasad-redis:6379/0"
    celery_broker_url: str = "redis://rasad-redis:6379/0"
    celery_result_backend: str = "redis://rasad-redis:6379/1"
    api_cors_origins: str = Field(default="http://localhost:5173")
    auth_secret_key: str = "change-this-local-development-secret"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 14
    ai_cloudflare_worker_url: str = ""
    ai_cloudflare_worker_secret: str = ""
    crawler_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    )
    crawler_page_timeout_ms: int = 30000
    crawler_max_concurrency: int = 3
    crawler_max_pages_per_source: int = 80
    crawler_respect_robots_txt: bool = True
    monitoring_distributed_fanout_enabled: bool = False
    monitoring_source_worker_concurrency: int = 5
    celery_visibility_timeout: int = 86400
    celery_source_visibility_timeout_seconds: int = 1800
    celery_analysis_visibility_timeout_seconds: int = 1800
    monitoring_analysis_worker_concurrency: int = 3
    deadline_scheduling_safety_margin_seconds: int = 300

    @property
    def max_deadline_horizon_seconds(self) -> int:
        horizon = self.celery_visibility_timeout - self.deadline_scheduling_safety_margin_seconds
        if horizon <= 0:
            raise ValueError(
                f"Invalid deadline horizon configuration: celery_visibility_timeout ({self.celery_visibility_timeout}s) "
                f"must exceed deadline_scheduling_safety_margin_seconds ({self.deadline_scheduling_safety_margin_seconds}s)"
            )
        return horizon

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.api_cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
