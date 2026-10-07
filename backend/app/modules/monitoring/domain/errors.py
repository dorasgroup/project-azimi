"""Monitoring domain business exceptions."""

from app.shared.domain.errors import ConflictError


class BotIdentityInUseError(ConflictError):
    pass
