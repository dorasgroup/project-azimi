from datetime import datetime

from pydantic import BaseModel, Field


from uuid import UUID

class ChatMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = Field(default=None, max_length=128)
    conversation_id: str | None = None
    category_ids: list[UUID] | None = None
    inline_citations: bool = False


class ChatSource(BaseModel):
    document_name: str | None = None
    content: str | None = None
    score: float | None = None
    document_id: str | None = None
    citation_id: str | None = None
    source_type: str | None = None
    source_url: str | None = None
    article_title: str | None = None


class ChatMessageResponse(BaseModel):
    id: str | None = None
    conversation_id: str | None
    message_id: str | None
    answer: str
    sources: list[ChatSource] = []
    created_at: datetime | None = None


class ChatHistoryMessage(BaseModel):
    id: str
    role: str
    content: str
    sources: list[ChatSource] = []


class ChatHistorySession(BaseModel):
    session_id: str
    title: str
    conversation_id: str | None
    messages: list[ChatHistoryMessage]
    updated_at: datetime
    category_ids: list[str] = []


class ChatHistoryResponse(BaseModel):
    items: list[ChatHistorySession]
