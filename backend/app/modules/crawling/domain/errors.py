"""Crawling domain business exceptions."""

from app.shared.domain.errors import ConflictError


class DuplicateSourceError(ConflictError):
    pass
