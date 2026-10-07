from pydantic import BaseModel, Field


class ConnectionCheck(BaseModel):
    name: str
    status: str
    message: str
    latency_ms: int | None = None


class ModelProviderRead(BaseModel):
    id: str
    name: str
    api_key_masked: str
    has_api_key: bool
    active_model: str | None = None
    active_embedding_model: str | None = None
    is_active: bool
    status: str


class ModelProviderUpdate(BaseModel):
    api_key: str | None = Field(default=None, max_length=500)
    active_model: str | None = Field(default=None, max_length=200)
    active_embedding_model: str | None = Field(default=None, max_length=200)


class ConnectionsStatus(BaseModel):
    providers: list[ModelProviderRead]
    checks: list[ConnectionCheck]
