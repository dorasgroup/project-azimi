from typing import Any, Generic, List, Optional, TypeVar
from pydantic import BaseModel, Field
from datetime import datetime, timezone

T = TypeVar("T")

class ErrorDetail(BaseModel):
    code: str
    field: Optional[str] = None
    detail: str

class StandardResponse(BaseModel, Generic[T]):
    success: bool
    data: Optional[T] = None
    message: str
    errors: List[ErrorDetail] = Field(default_factory=list)
    trace_id: str
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @classmethod
    def ok(cls, data: T, message: str = "Operation completed successfully.", trace_id: str = "") -> "StandardResponse[T]":
        return cls(
            success=True,
            data=data,
            message=message,
            trace_id=trace_id
        )

    @classmethod
    def error(cls, message: str, errors: List[ErrorDetail], trace_id: str = "") -> "StandardResponse[None]":
        return cls(
            success=False,
            data=None,
            message=message,
            errors=errors,
            trace_id=trace_id
        )
