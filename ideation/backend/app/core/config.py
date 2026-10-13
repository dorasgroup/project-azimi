from functools import lru_cache
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import quote, urlsplit
from uuid import UUID

from pydantic import AnyHttpUrl, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def parse_cors_allowed_origins(
    raw_value: str,
    *,
    app_env: str,
    allow_credentials: bool = True,
) -> list[str]:
    """Parse browser origins without silently widening cross-origin access."""
    environment = app_env.strip().lower()
    origins: list[str] = []
    for candidate in raw_value.split(","):
        origin = candidate.strip()
        if not origin:
            continue
        if origin == "*":
            if allow_credentials:
                raise ValueError("CORS wildcard cannot be used when credentials are enabled")
            origins.append(origin)
            continue

        try:
            parsed = urlsplit(origin)
            # Accessing port also validates malformed/non-numeric port values.
            _ = parsed.port
        except ValueError as exc:
            raise ValueError(f"Invalid CORS origin: {origin!r}") from exc

        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                f"Invalid CORS origin: {origin!r}; expected scheme://host[:port]"
            )

        hostname = parsed.hostname.lower()
        is_loopback = hostname == "localhost" or hostname.endswith(".localhost")
        try:
            is_loopback = is_loopback or ip_address(hostname).is_loopback
        except ValueError:
            pass
        if is_loopback and environment not in {"local", "development", "dev", "test"}:
            raise ValueError("Development CORS origins are not allowed outside development")
        if origin not in origins:
            origins.append(origin)

    if environment in {"production", "prod"} and not origins:
        raise ValueError(
            "CORS_ALLOWED_ORIGINS must contain at least one valid origin in production"
        )
    return origins


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Ideation RAG Backend"
    app_env: str = "local"
    app_debug: bool = True
    service_name: str = "ideation-backend"
    enable_prototype_routes: bool = False
    async_ingestion_enabled: bool = False
    trend_integration_enabled: bool = False
    trend_integration_token: SecretStr | None = None
    trend_integration_user_id: UUID | None = None
    site_article_sync_enabled: bool = False
    site_article_user_id: UUID | None = None
    site_article_category_name: str = "ازدواج"
    bale_integration_enabled: bool = False
    bale_content_upload_enabled: bool = False
    bale_text_upload_max_chars: int = 20_000
    bale_file_upload_max_mb: int = 20
    bale_bot_token: SecretStr | None = None
    bale_bot_token_file: Path | None = None
    bale_webhook_secret: SecretStr | None = None
    bale_webhook_secret_file: Path | None = None
    bale_integration_user_id: UUID | None = None
    bale_api_base_url: AnyHttpUrl = "https://tapi.bale.ai"
    bale_queue_name: str = "ideation.bale.messages.v1"
    bale_exchange_name: str = "ideation.bale.v1"
    bale_routing_key: str = "bale.message.v1"
    guest_access_token: SecretStr | None = None
    guest_access_token_file: Path | None = None
    guest_access_user_id: UUID | None = None
    log_dir: Path = Path("../storage/logs")

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/ideation_rag"

    jwt_secret_key: str = "change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30

    dify_base_url: AnyHttpUrl = "http://localhost/v1"
    dify_chat_api_key: str = "app-change-me"
    dify_dataset_api_key: str = "dataset-change-me"
    dify_dataset_id: UUID = UUID("ea1e709d-d175-4da4-8de4-3d2e5e1b5277")
    dify_weaviate_url: AnyHttpUrl = "http://localhost:8080"
    dify_weaviate_api_key: str = ""

    upload_min_size_mb: int = 0
    upload_max_size_mb: int = 500
    upload_storage_path: Path = Path("../storage/uploads")

    rabbitmq_host: str = "localhost"
    rabbitmq_port: int = 5672
    rabbitmq_user: str = "guest"
    rabbitmq_vhost: str = "/"
    rabbitmq_password: str = "guest"
    rabbitmq_password_file: Path | None = None
    ingestion_queue_name: str = "ideation.ingestion.jobs.v1"
    ingestion_exchange_name: str = "ideation.ingestion.v1"
    ingestion_routing_key: str = "ingestion.process.v1"
    ingestion_max_attempts: int = 3

    cors_allowed_origins_raw: str = Field(
        default="",
        alias="CORS_ALLOWED_ORIGINS",
    )

    @model_validator(mode="after")
    def validate_cors_configuration(self) -> "Settings":
        parse_cors_allowed_origins(
            self.cors_allowed_origins_raw,
            app_env=self.app_env,
            allow_credentials=True,
        )
        return self

    @property
    def cors_allowed_origins(self) -> list[str]:
        return parse_cors_allowed_origins(
            self.cors_allowed_origins_raw,
            app_env=self.app_env,
            allow_credentials=True,
        )

    @property
    def prototype_routes_enabled(self) -> bool:
        return self.enable_prototype_routes and self.app_env.strip().lower() in {
            "local",
            "development",
            "dev",
            "test",
        }

    @property
    def upload_min_size_bytes(self) -> int:
        return self.upload_min_size_mb * 1024 * 1024

    @property
    def upload_max_size_bytes(self) -> int:
        return self.upload_max_size_mb * 1024 * 1024

    @property
    def bale_file_upload_max_bytes(self) -> int:
        return min(self.bale_file_upload_max_mb * 1024 * 1024, self.upload_max_size_bytes)

    @property
    def celery_broker_url(self) -> str:
        password = self.rabbitmq_password
        if self.rabbitmq_password_file is not None:
            password = self.rabbitmq_password_file.read_text(encoding="utf-8").strip()
        vhost = "" if self.rabbitmq_vhost == "/" else quote(self.rabbitmq_vhost, safe="")
        return (
            f"amqp://{quote(self.rabbitmq_user, safe='')}:{quote(password, safe='')}@"
            f"{self.rabbitmq_host}:{self.rabbitmq_port}/{vhost}"
        )

    @staticmethod
    def _read_secret(value: SecretStr | None, path: Path | None) -> str:
        if path is not None:
            return path.read_text(encoding="utf-8").strip()
        return value.get_secret_value() if value is not None else ""

    @property
    def bale_bot_token_value(self) -> str:
        return self._read_secret(self.bale_bot_token, self.bale_bot_token_file)

    @property
    def bale_webhook_secret_value(self) -> str:
        return self._read_secret(self.bale_webhook_secret, self.bale_webhook_secret_file)

    @property
    def guest_access_token_value(self) -> str:
        return self._read_secret(self.guest_access_token, self.guest_access_token_file)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
