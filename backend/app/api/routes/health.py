from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.operations.health import HealthOperations
from app.bootstrap.providers import provide_health_operations
from app.schemas.health import HealthResponse

router = APIRouter(prefix="/health", tags=["health"])
Health = Annotated[HealthOperations, Depends(provide_health_operations)]


@router.get("", response_model=HealthResponse)
async def health(operations: Health) -> HealthResponse:
    return await operations.read()
