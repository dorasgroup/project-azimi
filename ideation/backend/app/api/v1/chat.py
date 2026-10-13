from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db_session
from app.models.chat_log import ChatLog
from app.schemas.auth import CurrentUserResponse
from app.schemas.chat import (
    ChatHistoryMessage,
    ChatHistoryResponse,
    ChatHistorySession,
    ChatMessageRequest,
    ChatMessageResponse,
)
from app.services.chat_citations import finalize_citations, without_citation_markers
from app.services.chat_errors import EMPTY_HISTORY_MESSAGE, EmptyModelAnswerError
from app.services.chat_service import ChatService, _sanitize_model_answer

router = APIRouter()


@router.get("/history", response_model=ChatHistoryResponse)
async def get_history(
    current_user: CurrentUserResponse = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    inline_citations: bool = False,
) -> ChatHistoryResponse:
    if current_user.role == "guest":
        return ChatHistoryResponse(items=[])
    result = await session.execute(
        select(ChatLog)
        .where(ChatLog.user_id == UUID(current_user.id))
        .order_by(ChatLog.created_at.desc())
        .limit(500)
    )

    grouped: dict[str, ChatHistorySession] = {}
    for log in reversed(result.scalars().all()):
        session_id = log.client_session_id or log.dify_conversation_id or f"legacy-{log.id}"
        history_session = grouped.get(session_id)
        if history_session is None:
            history_session = ChatHistorySession(
                session_id=session_id,
                title=log.query[:42],
                conversation_id=log.dify_conversation_id,
                messages=[],
                updated_at=log.created_at,
                category_ids=[str(item) for item in (log.category_ids or [])],
            )
            grouped[session_id] = history_session

        history_answer = _sanitize_model_answer(log.answer)
        visible_answer, _ = finalize_citations(history_answer, [])
        history_session.messages.extend(
            [
                ChatHistoryMessage(
                    id=f"{log.id}:user",
                    role="user",
                    content=log.query,
                ),
                ChatHistoryMessage(
                    id=f"{log.id}:assistant",
                    role="assistant",
                    content=(
                        (
                            history_answer if inline_citations
                            else without_citation_markers(history_answer)
                        ) if visible_answer
                        else EMPTY_HISTORY_MESSAGE
                    ),
                    sources=(log.sources or []) if visible_answer else [],
                ),
            ]
        )
        history_session.updated_at = log.created_at
        history_session.conversation_id = (
            log.dify_conversation_id or history_session.conversation_id
        )
        if log.category_ids:
            history_session.category_ids = [str(item) for item in log.category_ids]

    items = sorted(grouped.values(), key=lambda item: item.updated_at, reverse=True)
    return ChatHistoryResponse(items=items)


@router.delete("/history/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_history(
    session_id: str,
    current_user: CurrentUserResponse = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    if current_user.role == "guest":
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    user_id = UUID(current_user.id)
    conditions = [
        ChatLog.client_session_id == session_id,
        ChatLog.dify_conversation_id == session_id,
    ]
    if session_id.startswith("legacy-"):
        try:
            conditions.append(ChatLog.id == UUID(session_id.removeprefix("legacy-")))
        except ValueError:
            pass

    await session.execute(
        delete(ChatLog).where(
            ChatLog.user_id == user_id,
            or_(*conditions),
        )
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/messages", response_model=ChatMessageResponse)
async def send_message(
    payload: ChatMessageRequest,
    current_user: CurrentUserResponse = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ChatMessageResponse:
    try:
        return await ChatService(session).ask(
            user_id=UUID(current_user.id),
            message=payload.message,
            client_session_id=payload.session_id,
            conversation_id=payload.conversation_id,
            category_ids=payload.category_ids,
            allow_all_documents=current_user.role in {"super_admin", "guest"},
            include_inline_citations=payload.inline_citations,
        )
    except EmptyModelAnswerError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except httpx.HTTPStatusError as exc:
        detail = f"Dify returned an error: {exc.response.status_code}"
        try:
            dify_error = exc.response.json()
            if isinstance(dify_error, dict) and dify_error.get("message"):
                detail = str(dify_error["message"])
        except ValueError:
            if exc.response.text:
                detail = exc.response.text
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=detail,
        ) from exc
    except httpx.TransportError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not connect to Dify: {exc}",
        ) from exc
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Dify timed out",
        ) from exc
