"""Read-only version information route."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.operations.version import VersionOperations
from app.bootstrap.providers import provide_version_operations
from app.schemas.version import VersionResponse

router = APIRouter(prefix="/version", tags=["version"])
Version = Annotated[VersionOperations, Depends(provide_version_operations)]


@router.get("", response_model=VersionResponse)
def get_version(operations: Version) -> VersionResponse:
    return operations.read()
