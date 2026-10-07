from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import (
    domain_exception_handler,
    http_exception_handler,
    validation_exception_handler,
)
from app.api.middleware import RequestLoggingMiddleware
from app.api.router import api_router
from app.core.config import settings
from app.core.logging import configure_logging
from app.db.health import close_database, open_database
from app.shared.domain.errors import DomainError

configure_logging()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await open_database()
    yield
    await close_database()


app = FastAPI(
    title=settings.project_name,
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestLoggingMiddleware)
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(DomainError, domain_exception_handler)
app.include_router(api_router, prefix="/api/v1")
