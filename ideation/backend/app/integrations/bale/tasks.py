import asyncio
import logging
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from time import monotonic

from celery import Task

from app.core.config import settings
from app.core.logging import log_event
from app.db.session import get_sessionmaker
from app.integrations.bale.celery_app import BALE_TASK_NAME, celery_app
from app.integrations.bale.client import BaleClient
from app.integrations.bale.keyboard import change_category_keyboard
from app.integrations.bale.repository import BaleRepository
from app.integrations.bale.upload_service import BaleContentUploadService
from app.middleware.request_logging import valid_request_id
from app.models.category import Category
from app.observability.context import reset_request_id, set_request_id
from app.repositories.category_repository import CategoryRepository
from app.services.chat_service import ChatService

logger = logging.getLogger(__name__)
MAX_BALE_MESSAGE_LENGTH = 3900
BALE_MEMORY_TTL = timedelta(minutes=15)
BALE_PROGRESS_MIN_INTERVAL_SECONDS = 0.7
BALE_PROGRESS_MIN_GROWTH = 40


class BaleProcessingError(RuntimeError):
    pass


def split_bale_message(text: str, limit: int = MAX_BALE_MESSAGE_LENGTH) -> list[str]:
    value = text.strip()
    if not value:
        return ["پاسخی برای نمایش دریافت نشد."]
    chunks: list[str] = []
    while len(value) > limit:
        split_at = max(value.rfind("\n", 0, limit), value.rfind(" ", 0, limit))
        if split_at < limit // 2:
            split_at = limit
        chunks.append(value[:split_at].rstrip())
        value = value[split_at:].lstrip()
    if value:
        chunks.append(value)
    return chunks


async def _get_available_category(
    category_repo: CategoryRepository,
    category_id,
) -> Category | None:
    if category_id is None:
        return None
    return next(
        (item for item in await category_repo.list_available_in_bale() if item.id == category_id),
        None,
    )


async def process_bale_update(
    update_id: int,
    client: BaleClient | None = None,
    request_id: str | None = None,
) -> str:
    async with get_sessionmaker()() as session:
        repo = BaleRepository(session)
        update = await repo.get_update(update_id)
        if update is None:
            return "missing"
        if update.status == "completed":
            return "already_completed"
        if update.status == "awaiting_category":
            return "awaiting_category"
        if (
            update.kind != "message"
            or update.chat_id is None
            or (not update.text and not update.document_file_id)
        ):
            update.status = "ignored"
            await session.commit()
            return "ignored"

        update.status = "processing"
        update.attempt_count += 1
        update.error_code = None
        update.error_message = None
        await session.commit()

        conversation = await repo.get_conversation(update.chat_id)
        if conversation is None:
            update.status = "failed"
            update.error_code = "conversation_missing"
            update.error_message = "The Bale conversation does not exist"
            await session.commit()
            raise BaleProcessingError("Bale conversation is unavailable")
        if update.intent == "upload":
            return await BaleContentUploadService(session, client=client).process(
                update,
                conversation,
                request_id=request_id,
            )

        category = await _get_available_category(
            CategoryRepository(session),
            update.category_id_snapshot
            or (conversation.selected_category_id if conversation else None),
        )
        if category is None:
            update.status = "failed"
            update.error_code = "category_unavailable"
            update.error_message = "The selected category is not available in Bale"
            await session.commit()
            raise BaleProcessingError("Bale category is unavailable")
        if settings.bale_integration_user_id is None:
            update.status = "failed"
            update.error_code = "integration_user_missing"
            update.error_message = "The Bale integration user is not configured"
            await session.commit()
            raise BaleProcessingError("Bale integration user is not configured")

        bale = client or BaleClient()
        progress_message_id = update.progress_message_id
        if update.answer_text is None:
            with suppress(Exception):
                await bale.send_chat_action(update.chat_id)
            if progress_message_id is None:
                with suppress(Exception):
                    progress_message = await bale.send_message(
                        update.chat_id,
                        "در حال ارسال ایده",
                        reply_to_message_id=update.message_id,
                    )
                    if isinstance(progress_message, dict):
                        candidate_id = progress_message.get("message_id")
                        if isinstance(candidate_id, int):
                            progress_message_id = candidate_id
                            update.progress_message_id = candidate_id
                            await session.commit()

            last_progress_at = 0.0
            last_progress_length = 0

            async def publish_progress(answer: str) -> None:
                nonlocal last_progress_at, last_progress_length
                if progress_message_id is None:
                    return
                preview = answer[:MAX_BALE_MESSAGE_LENGTH].strip()
                if not preview:
                    return
                now = monotonic()
                if (
                    last_progress_length
                    and len(preview) - last_progress_length < BALE_PROGRESS_MIN_GROWTH
                    and now - last_progress_at < BALE_PROGRESS_MIN_INTERVAL_SECONDS
                ):
                    return
                try:
                    await bale.edit_message_text(update.chat_id, progress_message_id, preview)
                except Exception:
                    return
                last_progress_at = now
                last_progress_length = len(preview)

            response = await ChatService(session).ask(
                user_id=settings.bale_integration_user_id,
                message=update.text,
                client_session_id=(
                    update.client_session_id_snapshot or conversation.client_session_id
                ),
                conversation_id=(
                    update.dify_conversation_id_snapshot
                    if update.intent_version_snapshot is not None
                    else conversation.dify_conversation_id
                ),
                category_ids=[category.id],
                allow_all_documents=True,
                on_answer_progress=publish_progress,
            )
            update.answer_text = response.answer
            if (
                update.intent_version_snapshot is None
                or (
                    conversation.intent_version == update.intent_version_snapshot
                    and conversation.client_session_id
                    == update.client_session_id_snapshot
                )
            ):
                conversation.dify_conversation_id = response.conversation_id
                conversation.memory_expires_at = datetime.now(timezone.utc) + BALE_MEMORY_TTL
            await session.commit()

        chunks = split_bale_message(update.answer_text)
        start_index = 0
        if progress_message_id is not None:
            try:
                await bale.edit_message_text(
                    update.chat_id,
                    progress_message_id,
                    chunks[0],
                    reply_markup=change_category_keyboard() if len(chunks) == 1 else None,
                )
                start_index = 1
            except Exception:
                start_index = 0
        for index in range(start_index, len(chunks)):
            chunk = chunks[index]
            await bale.send_message(
                update.chat_id,
                chunk,
                reply_markup=change_category_keyboard() if index == len(chunks) - 1 else None,
                reply_to_message_id=update.message_id if index == 0 else None,
            )
        update.status = "completed"
        update.error_code = None
        update.error_message = None
        await session.commit()
        return "completed"


async def mark_bale_update_failed(update_id: int, error_code: str) -> None:
    async with get_sessionmaker()() as session:
        update = await BaleRepository(session).get_update(update_id)
        if update is None or update.status == "completed":
            return
        update.status = "failed"
        update.error_code = error_code
        update.error_message = "Bale message processing failed"
        await session.commit()


async def notify_terminal_failure(update_id: int) -> None:
    async with get_sessionmaker()() as session:
        repo = BaleRepository(session)
        update = await repo.get_update(update_id)
        if update is None or update.chat_id is None:
            return
        message = "پاسخ‌گویی موقتاً با مشکل روبه‌رو شد. لطفاً کمی بعد دوباره تلاش کنید."
        if update.intent == "upload":
            message = "ثبت محتوا موقتاً با مشکل روبه‌رو شد. لطفاً دوباره تلاش کنید."
            conversation = await repo.get_conversation(update.chat_id)
            if conversation is not None:
                conversation.pending_upload_update_id = None
                repo.set_mode(conversation, "menu")
                await session.commit()
        with suppress(Exception):
            client = BaleClient()
            if update.progress_message_id is not None:
                await client.edit_message_text(
                    update.chat_id,
                    update.progress_message_id,
                    message,
                )
            else:
                await client.send_message(
                    update.chat_id,
                    message,
                    reply_to_message_id=update.message_id,
                )


@celery_app.task(
    bind=True,
    name=BALE_TASK_NAME,
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=2,
    ignore_result=True,
)
def process_bale_task(self: Task, update_id: int) -> None:
    headers = getattr(self.request, "headers", {}) or {}
    supplied_request_id = headers.get("request_id")
    request_id = supplied_request_id if valid_request_id(supplied_request_id) else None
    context_token = set_request_id(request_id)
    try:
        try:
            outcome = asyncio.run(process_bale_update(update_id, request_id=request_id))
            log_event(logger, logging.INFO, "bale_task_completed", outcome=outcome)
        except Exception as exc:
            asyncio.run(mark_bale_update_failed(update_id, "processing_failed"))
            retries = int(getattr(self.request, "retries", 0))
            if retries < self.max_retries:
                raise self.retry(
                    exc=BaleProcessingError("Bale processing failed"),
                    countdown=5 * (2**retries),
                ) from exc
            asyncio.run(notify_terminal_failure(update_id))
            log_event(
                logger,
                logging.ERROR,
                "bale_task_failed",
                outcome="terminal_failure",
                error_type=type(exc).__name__,
            )
    finally:
        reset_request_id(context_token)
