"""Document processing and persistence adapters."""

__all__ = ["ContentProcessorAdapter", "SqlAlchemyDocumentRepository"]


def __getattr__(name: str):
    if name == "ContentProcessorAdapter":
        from app.modules.documents.infrastructure.content_processor_adapter import (
            ContentProcessorAdapter,
        )

        return ContentProcessorAdapter
    if name == "SqlAlchemyDocumentRepository":
        from app.modules.documents.infrastructure.repository import SqlAlchemyDocumentRepository

        return SqlAlchemyDocumentRepository
    raise AttributeError(name)
