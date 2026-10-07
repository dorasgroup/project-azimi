"""Version transport operation returning build and release metadata."""

from typing import Optional, Protocol

from app.schemas.version import VersionResponse


class VersionMetadataProvider(Protocol):
    def get_version(self) -> str: ...
    def get_git_sha(self) -> str: ...
    def get_source_digest(self) -> str: ...
    def get_build_time(self) -> str: ...
    def get_migration_revision(self) -> Optional[str]: ...


class VersionOperations:
    def __init__(self, provider: VersionMetadataProvider) -> None:
        self._provider = provider

    def read(self) -> VersionResponse:
        return VersionResponse(
            version=self._provider.get_version(),
            git_sha=self._provider.get_git_sha(),
            source_digest=self._provider.get_source_digest(),
            build_time=self._provider.get_build_time(),
            migration_revision=self._provider.get_migration_revision(),
        )
