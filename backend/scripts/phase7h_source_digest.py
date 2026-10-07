"""Deterministic digest of source files copied into the Phase 7H test image."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FILES = ("pyproject.toml", "alembic.ini", "Dockerfile", "Dockerfile.worker")
DIRECTORIES = ("app", "tests", "scripts", "migrations")
EXCLUDED_PARTS = {"__pycache__", ".pytest_cache", ".ruff_cache"}


def included(path: Path) -> bool:
    relative = path.relative_to(ROOT)
    return not (set(relative.parts) & EXCLUDED_PARTS) and path.suffix not in {".pyc", ".pyo"}


def digest() -> tuple[str, int]:
    paths = [ROOT / name for name in FILES]
    for directory in DIRECTORIES:
        paths.extend(path for path in (ROOT / directory).rglob("*") if path.is_file() and included(path))
    paths = sorted(paths, key=lambda path: path.relative_to(ROOT).as_posix())
    hasher = hashlib.sha256()
    for path in paths:
        relative = path.relative_to(ROOT).as_posix().encode("utf-8")
        content = path.read_bytes()
        hasher.update(len(relative).to_bytes(4, "big"))
        hasher.update(relative)
        hasher.update(len(content).to_bytes(8, "big"))
        hasher.update(content)
    return hasher.hexdigest(), len(paths)


if __name__ == "__main__":
    fingerprint, count = digest()
    print(json.dumps({"sha256": fingerprint, "files": count}))
