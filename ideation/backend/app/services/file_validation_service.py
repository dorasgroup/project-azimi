from pathlib import Path

from app.core.config import settings

DOCUMENT_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".txt", ".csv", ".md"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a"}
ALLOWED_EXTENSIONS = DOCUMENT_EXTENSIONS | AUDIO_EXTENSIONS


class FileValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class FileValidationService:
    def validate_filename(self, filename: str | None) -> str:
        if not filename:
            raise FileValidationError("MISSING_FILENAME", "File name is required")
        extension = Path(filename).suffix.lower()
        if extension not in ALLOWED_EXTENSIONS:
            raise FileValidationError("UNSUPPORTED_FILE_TYPE", "This file type is not supported")
        return extension

    def validate_size(self, size_bytes: int) -> None:
        if size_bytes < settings.upload_min_size_bytes:
            raise FileValidationError(
                "FILE_TOO_SMALL",
                "File is smaller than the minimum allowed size",
            )
        if size_bytes > settings.upload_max_size_bytes:
            raise FileValidationError(
                "FILE_TOO_LARGE",
                "File is larger than the maximum allowed size",
            )

    def is_audio(self, extension: str) -> bool:
        return extension in AUDIO_EXTENSIONS
