from typing import Any, Dict, Optional

class DomainException(Exception):
    def __init__(self, message: str, code: str, status_code: int = 400, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details or {}

class ResourceNotFoundException(DomainException):
    def __init__(self, resource_name: str, resource_id: Any):
        super().__init__(
            message=f"{resource_name} with id {resource_id} not found.",
            code=f"{resource_name.upper()}_NOT_FOUND",
            status_code=404
        )

class ValidationException(DomainException):
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=message,
            code="VALIDATION_ERROR",
            status_code=422,
            details=details
        )

class AuthenticationException(DomainException):
    def __init__(self, message: str = "Authentication failed."):
        super().__init__(
            message=message,
            code="UNAUTHORIZED",
            status_code=401
        )

class AuthorizationException(DomainException):
    def __init__(self, message: str = "You do not have permission to perform this action."):
        super().__init__(
            message=message,
            code="FORBIDDEN",
            status_code=403
        )

class ConflictException(DomainException):
    def __init__(self, message: str, code: str = "CONFLICT"):
        super().__init__(
            message=message,
            code=code,
            status_code=409
        )
