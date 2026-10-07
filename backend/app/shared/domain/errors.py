"""Business errors that can be translated by an entrypoint."""


class DomainError(Exception):
    """Base class for expected domain and application failures."""


class NotFoundError(DomainError):
    """Raised when a requested resource does not exist."""


class ForbiddenError(DomainError):
    """Raised when an actor is not allowed to perform an operation."""


class ConflictError(DomainError):
    """Raised when an operation conflicts with the current state."""


class ValidationError(DomainError):
    """Raised when an input or state transition is invalid."""


class AuthenticationError(DomainError):
    """Raised when credentials or the authenticated account are invalid."""
