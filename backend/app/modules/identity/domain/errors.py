"""Identity domain business exceptions."""

from app.shared.domain.errors import AuthenticationError, ConflictError


class AuthError(AuthenticationError):
    pass


class DuplicateUserError(ConflictError):
    pass
