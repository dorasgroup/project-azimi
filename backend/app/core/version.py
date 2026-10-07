"""Runtime release and build metadata provider."""

import hashlib
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

GIT_SHA_REGEX = re.compile(r"^[0-9a-f]{7,40}$", re.IGNORECASE)
HEX_DIGEST_REGEX = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)


@lru_cache(maxsize=1)
def calculate_source_digest(root_dir: Optional[str] = None) -> str:
    """Deterministically compute SHA256 over all source python files and project descriptors."""
    if root_dir is None:
        root_path = Path(__file__).resolve().parents[2]
    else:
        root_path = Path(root_dir)

    hasher = hashlib.sha256()
    files_to_hash: list[Path] = []

    for base in [root_path / "pyproject.toml", root_path / "alembic.ini"]:
        if base.is_file():
            files_to_hash.append(base)

    app_dir = root_path / "app"
    if app_dir.is_dir():
        for p in sorted(app_dir.rglob("*.py")):
            files_to_hash.append(p)

    if not files_to_hash:
        return "unknown"

    for file_path in sorted(files_to_hash):
        rel_path = file_path.relative_to(root_path).as_posix()
        hasher.update(rel_path.encode("utf-8"))
        hasher.update(file_path.read_bytes())

    return hasher.hexdigest()


class AppVersionMetadataProvider:
    """Provides release metadata from environment or build configuration."""

    def __init__(
        self,
        default_version: str = "0.1.0",
        default_git_sha: str = "unknown",
        default_build_time: str = "unknown",
        default_migration_revision: Optional[str] = None,
    ) -> None:
        self._default_version = default_version
        self._default_git_sha = default_git_sha
        self._default_build_time = default_build_time
        self._default_migration_revision = default_migration_revision

    def get_version(self) -> str:
        return os.getenv("APP_VERSION", self._default_version)

    def get_git_sha(self) -> str:
        raw_sha = os.getenv("GIT_SHA", "").strip()
        if raw_sha and GIT_SHA_REGEX.match(raw_sha):
            return raw_sha
        return self._default_git_sha

    def get_source_digest(self) -> str:
        env_digest = os.getenv("SOURCE_DIGEST", "").strip()
        if env_digest and HEX_DIGEST_REGEX.match(env_digest):
            return env_digest
        return calculate_source_digest()

    def get_build_time(self) -> str:
        return os.getenv("BUILD_TIME", self._default_build_time)

    def get_migration_revision(self) -> Optional[str]:
        val = os.getenv("MIGRATION_REVISION")
        if val:
            return val
        return self._default_migration_revision
