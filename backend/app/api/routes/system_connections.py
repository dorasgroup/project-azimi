from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import Principal, require_admin
from app.bootstrap.providers import (
    provide_connection_health,
    provide_provider_settings,
)
from app.api.operations.system_connections import (
    ConnectionHealthOperations,
    ProviderSettingsOperations,
)
from app.schemas.system_connection import (
    ConnectionsStatus,
    ModelProviderRead,
    ModelProviderUpdate,
)

router = APIRouter(prefix="/system/connections", tags=["system-connections"])
Health = Annotated[ConnectionHealthOperations, Depends(provide_connection_health)]
ProviderSettings = Annotated[ProviderSettingsOperations, Depends(provide_provider_settings)]


@router.get("", response_model=ConnectionsStatus)
async def get_connections(
    _: Annotated[Principal, Depends(require_admin)], health: Health
) -> ConnectionsStatus:
    return await health.summary()


@router.put("/model-providers/{provider_id}", response_model=ModelProviderRead)
async def update_provider(
    provider_id: str,
    payload: ModelProviderUpdate,
    _: Annotated[Principal, Depends(require_admin)],
    settings_operations: ProviderSettings,
) -> ModelProviderRead:
    return await settings_operations.update(
        provider_id, payload.api_key, payload.active_model, payload.active_embedding_model
    )


@router.post("/model-providers/{provider_id}/activate", response_model=list[ModelProviderRead])
async def activate_provider_route(
    provider_id: str,
    _: Annotated[Principal, Depends(require_admin)],
    settings_operations: ProviderSettings,
) -> list[ModelProviderRead]:
    return await settings_operations.activate(provider_id)


@router.delete("/model-providers/{provider_id}", response_model=list[ModelProviderRead])
async def delete_provider_route(
    provider_id: str,
    _: Annotated[Principal, Depends(require_admin)],
    settings_operations: ProviderSettings,
) -> list[ModelProviderRead]:
    return await settings_operations.delete(provider_id)


@router.post("/check", response_model=ConnectionsStatus)
async def check_all(
    _: Annotated[Principal, Depends(require_admin)], health: Health
) -> ConnectionsStatus:
    return await health.summary()
