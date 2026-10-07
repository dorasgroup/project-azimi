"""Safe local storage for generated report artifacts."""

import os
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import UUID

from app.modules.reports.domain import RenderedReport


class LocalReportFileStorage:
    def __init__(self, root: Path | str = "/storage/reports") -> None:
        self._root = Path(root).resolve()

    def path_for(self, report_id: UUID, export_format: str, filename: str | None = None) -> Path:
        if export_format not in {"pdf", "docx"}:
            raise ValueError(f"Unsupported export format: {export_format}")
        safe_name = Path(filename or f"{report_id}.{export_format}").name
        if (
            not safe_name.startswith(str(report_id))
            or Path(safe_name).suffix != f".{export_format}"
        ):
            raise ValueError("Report filename does not match its id or export format")
        path = (self._root / safe_name).resolve()
        if self._root not in path.parents:
            raise ValueError("Report path escapes storage root")
        return path

    def save(self, report_id: UUID, rendered: RenderedReport) -> Path:
        export_format = Path(rendered.filename).suffix.removeprefix(".")
        path = self.path_for(report_id, export_format, rendered.filename)
        self._atomic_write(path, rendered.content)
        return path

    def save_deterministic(self, report_id: UUID, export_format: str, content: bytes) -> Path:
        """Persist a retry-stable artifact without changing legacy filenames."""
        path = self.path_for(report_id, export_format)
        self._atomic_write(path, content)
        return path

    def save_content_addressed(
        self, generation_id: UUID, export_format: str, content: bytes,
    ) -> tuple[str, str, int]:
        """A stale renderer cannot overwrite a different accepted artifact."""
        checksum = sha256(content).hexdigest()
        key = f"{generation_id}-{checksum}.{export_format}"
        path = self.path_for(generation_id, export_format, key)
        if not self.verify(generation_id, export_format, key, checksum, len(content)):
            self._atomic_write(path, content)
        return key, checksum, len(content)

    def verify(
        self, generation_id: UUID, export_format: str, key: str,
        checksum: str, size: int,
    ) -> bool:
        if len(checksum) != 64 or any(char not in "0123456789abcdef" for char in checksum):
            return False
        if key != f"{generation_id}-{checksum}.{export_format}":
            return False
        path = self.path_for(generation_id, export_format, key)
        if not path.is_file() or path.stat().st_size != size:
            return False
        digest = sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest() == checksum

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="wb", prefix=f".{path.name}.", suffix=".tmp",
                dir=path.parent, delete=False,
            ) as handle:
                temporary = Path(handle.name)
                os.chmod(temporary, 0o600)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            temporary = None
            if os.name == "posix":
                directory_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def exists(self, report_id: UUID, export_format: str) -> bool:
        return self.path_for(report_id, export_format).is_file()

    def delete(self, path: str | Path) -> None:
        candidate = Path(path).resolve()
        if self._root not in candidate.parents:
            raise ValueError("Refusing to delete a path outside report storage")
        if candidate.is_file():
            candidate.unlink()
