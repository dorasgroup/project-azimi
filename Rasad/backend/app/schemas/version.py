"""Version and build metadata response schema."""

from typing import Optional

from pydantic import BaseModel, ConfigDict


class VersionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    git_sha: str
    source_digest: str
    build_time: str
    migration_revision: Optional[str] = None
