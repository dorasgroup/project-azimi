from hmac import compare_digest
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.integrations.bale.client import BaleClient
from app.integrations.bale.keyboard import (
    MENU_REPOSITORIES_CALLBACK,
    MENU_UPLOAD_CALLBACK,
    category_keyboard,
    main_menu_keyboard,
    parse_categories_page_callback,
    parse_category_callback,
    parse_upload_categories_page_callback,
    parse_upload_category_callback,
    upload_category_keyboard,
)
from app.integrations.bale.publisher import BalePublishError, publish_bale_update
from app.integrations.bale.repository import BaleRepository
from app.integrations.bale.schemas import BaleUpdatePayload, BaleWebhookResult
from app.repositories.category_repository import CategoryRepository

WELCOME_TEXT = "سلام! چه کاری می‌خواهید انجام دهید؟"
NO_CATEGORY_TEXT = "ابتدا مخزن موضوعی مورد نظر خود را انتخاب کنید."
NO_AVAILABLE_CATEGORIES_TEXT = "در حال حاضر مخزن فعالی برای گفت‌وگو در بله وجود ندارد."
UPLOAD_COMING_SOON_TEXT = "قابلیت بارگذاری محتوا در حال آماده‌سازی است و به‌زودی فعال می‌شود."


def require_bale_webhook(secret: str | None) -> None:
    if not settings.bale_integration_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    try:
        configured = settings.bale_webhook_secret_value
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Bale integration is not configured",
        ) from exc
    if not configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Bale integration is not configured",
        )
    if secret is None or len(secret) > 256 or not compare_digest(secret, configured):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid integration credential",
        )


class BaleWebhookService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        client: BaleClient | None = None,
    ) -> None:
        self.session = session
        self.client = client or BaleClient()
        self.repo = BaleRepository(session)
        self.categories = CategoryRepository(session)

    async def handle(
        self,
        payload: BaleUpdatePayload,
        *,
        request_id: str | None,
    ) -> BaleWebhookResult:
        values = self._update_values(payload)
        update, duplicate = await self.repo.claim_update(values)
        if duplicate and update.status != "failed":
            await self.session.rollback()
            return BaleWebhookResult(duplicate=True)

        if payload.callback_query is not None:
            await self._handle_callback(payload, request_id=request_id)
            update.intent = "callback"
            update.status = "completed"
            await self.session.commit()
            return BaleWebhookResult(duplicate=duplicate)

        message = payload.message
        if message is None or message.from_user is None or message.from_user.is_bot:
            update.status = "ignored"
            await self.session.commit()
            return BaleWebhookResult(duplicate=duplicate)
        if message.chat.type != "private":
            update.status = "ignored"
            await self.session.commit()
            return BaleWebhookResult(duplicate=duplicate)

        text = (message.text or "").strip()
        conversation = await self.repo.get_or_create_conversation(
            chat_id=message.chat.id,
            bale_user_id=message.from_user.id,
            chat_type=message.chat.type,
        )
        command = text.split(maxsplit=1)[0].split("@", 1)[0].lower() if text else ""
        if command in {"/start", "/menu", "/help"}:
            self.repo.set_mode(conversation, "menu")
            await self._send_main_menu(message.chat.id, welcome=command == "/start")
            update.intent = "command"
            update.status = "completed"
            await self.session.commit()
            return BaleWebhookResult(duplicate=duplicate)
        if command == "/categories":
            self.repo.set_mode(conversation, "select_repository")
            await self._send_categories(message.chat.id, page=0, welcome=False)
            update.intent = "command"
            update.status = "completed"
            await self.session.commit()
            return BaleWebhookResult(duplicate=duplicate)
        if command == "/cancel":
            conversation.pending_upload_update_id = None
            self.repo.set_mode(conversation, "menu")
            await self._send_main_menu(message.chat.id, welcome=False)
            update.intent = "command"
            update.status = "completed"
            await self.session.commit()
            return BaleWebhookResult(duplicate=duplicate)
        if command == "/upload":
            update.intent = "command"
            if settings.bale_content_upload_enabled:
                conversation.pending_upload_update_id = None
                self.repo.set_mode(conversation, "upload")
                await self.client.send_message(
                    message.chat.id,
                    "متن یا فایل PDF، DOC، DOCX یا TXT خود را ارسال کنید. "
                    f"سقف فایل {settings.bale_file_upload_max_mb} مگابایت است. "
                    "برای انصراف /cancel را بفرستید.",
                )
            else:
                self.repo.set_mode(conversation, "menu")
                await self.client.send_message(
                    message.chat.id,
                    UPLOAD_COMING_SOON_TEXT,
                    reply_markup=main_menu_keyboard(),
                )
            update.status = "completed"
            await self.session.commit()
            return BaleWebhookResult(duplicate=duplicate)
        if command == "/new":
            self.repo.reset_conversation(conversation)
            await self.client.send_message(
                message.chat.id,
                "گفت‌وگوی جدید آغاز شد. سؤال بعدی را درباره مخزن انتخاب‌شده بپرسید.",
            )
            update.status = "completed"
            await self.session.commit()
            return BaleWebhookResult(duplicate=duplicate)
        if not text and message.document is None:
            await self.client.send_message(
                message.chat.id,
                "برای بارگذاری فایل، ابتدا از منوی اصلی «بارگذاری محتوا» را انتخاب کنید.",
                reply_markup=main_menu_keyboard(),
            )
            update.status = "completed"
            await self.session.commit()
            return BaleWebhookResult(duplicate=duplicate)

        if conversation.mode == "upload":
            self.repo.set_mode(conversation, "upload_processing")
            conversation.pending_upload_update_id = update.update_id
            update.intent = "upload"
            update.intent_version_snapshot = conversation.intent_version
            try:
                await self._queue_update(update, request_id=request_id)
            except HTTPException:
                conversation.pending_upload_update_id = None
                self.repo.set_mode(conversation, "upload")
                await self.session.commit()
                raise
            return BaleWebhookResult(duplicate=duplicate)
        if conversation.mode == "upload_processing":
            await self.client.send_message(
                message.chat.id,
                "محتوای قبلی هنوز در حال پردازش است. لطفاً منتظر نتیجه بمانید.",
            )
            update.status = "completed"
            await self.session.commit()
            return BaleWebhookResult(duplicate=duplicate)
        if conversation.mode == "upload_category":
            await self._send_pending_upload_categories(conversation)
            update.status = "completed"
            await self.session.commit()
            return BaleWebhookResult(duplicate=duplicate)
        if message.document is not None:
            await self.client.send_message(
                message.chat.id,
                "برای بارگذاری فایل، ابتدا از منوی اصلی «بارگذاری محتوا» را انتخاب کنید.",
                reply_markup=main_menu_keyboard(),
            )
            update.status = "completed"
            await self.session.commit()
            return BaleWebhookResult(duplicate=duplicate)
        if conversation.mode != "chat":
            if conversation.mode == "select_repository":
                await self._send_categories(message.chat.id, page=0, welcome=False)
            else:
                await self._send_main_menu(message.chat.id, welcome=False)
            update.status = "completed"
            await self.session.commit()
            return BaleWebhookResult(duplicate=duplicate)

        if not await self._selected_category_is_available(conversation.selected_category_id):
            conversation.selected_category_id = None
            conversation.dify_conversation_id = None
            conversation.memory_expires_at = None
            self.repo.set_mode(conversation, "select_repository")
            await self._send_categories(message.chat.id, page=0, welcome=False)
            update.status = "completed"
            await self.session.commit()
            return BaleWebhookResult(duplicate=duplicate)

        if (
            conversation.memory_expires_at is not None
            and conversation.memory_expires_at <= datetime.now(timezone.utc)
        ):
            self.repo.expire_memory(conversation)

        update.intent = "chat"
        update.category_id_snapshot = conversation.selected_category_id
        update.client_session_id_snapshot = conversation.client_session_id
        update.dify_conversation_id_snapshot = conversation.dify_conversation_id
        update.intent_version_snapshot = conversation.intent_version
        await self._queue_update(update, request_id=request_id)
        return BaleWebhookResult(duplicate=duplicate)

    async def _handle_callback(
        self,
        payload: BaleUpdatePayload,
        *,
        request_id: str | None,
    ) -> None:
        callback = payload.callback_query
        if callback is None:
            return
        if callback.message is None or callback.message.chat.type != "private":
            await self.client.answer_callback_query(callback.id)
            return
        chat_id = callback.message.chat.id
        await self.client.answer_callback_query(callback.id)

        conversation = await self.repo.get_or_create_conversation(
            chat_id=chat_id,
            bale_user_id=callback.from_user.id,
            chat_type=callback.message.chat.type,
        )

        if callback.data == "menu:home":
            self.repo.set_mode(conversation, "menu")
            await self._send_main_menu(chat_id, welcome=False)
            return
        if callback.data == MENU_REPOSITORIES_CALLBACK:
            self.repo.set_mode(conversation, "select_repository")
            await self._send_categories(chat_id, page=0, welcome=False)
            return
        if callback.data == MENU_UPLOAD_CALLBACK:
            if settings.bale_content_upload_enabled:
                conversation.pending_upload_update_id = None
                self.repo.set_mode(conversation, "upload")
                await self.client.send_message(
                    chat_id,
                    "متن یا فایل PDF، DOC، DOCX یا TXT خود را ارسال کنید. "
                    f"سقف فایل {settings.bale_file_upload_max_mb} مگابایت است. "
                    "برای انصراف /cancel را بفرستید.",
                )
            else:
                self.repo.set_mode(conversation, "menu")
                await self.client.send_message(
                    chat_id,
                    UPLOAD_COMING_SOON_TEXT,
                    reply_markup=main_menu_keyboard(),
                )
            return

        upload_page = parse_upload_categories_page_callback(callback.data)
        if upload_page is not None:
            pending_update_id, page = upload_page
            if conversation.pending_upload_update_id != pending_update_id:
                await self.client.send_message(chat_id, "این درخواست دیگر فعال نیست.")
                return
            categories = await self.categories.list_all()
            if not categories:
                await self.client.send_message(chat_id, NO_AVAILABLE_CATEGORIES_TEXT)
                return
            await self.client.send_message(
                chat_id,
                "دسته مناسب محتوا را انتخاب کنید:",
                reply_markup=upload_category_keyboard(
                    categories,
                    update_id=pending_update_id,
                    page=page,
                ),
            )
            return

        upload_selection = parse_upload_category_callback(callback.data)
        if upload_selection is not None:
            pending_update_id, category_id = upload_selection
            await self._resume_upload_with_category(
                conversation,
                pending_update_id=pending_update_id,
                category_id=category_id,
                request_id=request_id,
            )
            return

        page = parse_categories_page_callback(callback.data)
        if page is not None:
            self.repo.set_mode(conversation, "select_repository")
            await self._send_categories(chat_id, page=page, welcome=False)
            return
        if callback.data == "new:chat":
            if not await self._selected_category_is_available(
                conversation.selected_category_id
            ):
                conversation.selected_category_id = None
                self.repo.set_mode(conversation, "select_repository")
                await self._send_categories(chat_id, page=0, welcome=False)
                return
            self.repo.reset_conversation(conversation)
            self.repo.set_mode(conversation, "chat")
            await self.client.send_message(
                chat_id,
                "گفت‌وگوی جدید آغاز شد. سؤال بعدی را بپرسید.",
            )
            return

        category_id = parse_category_callback(callback.data)
        available = await self.categories.list_available_in_bale()
        selected = next((item for item in available if item.id == category_id), None)
        if selected is None:
            await self.client.send_message(chat_id, "این مخزن دیگر در دسترس نیست.")
            await self._send_categories(chat_id, page=0, welcome=False)
            return
        self.repo.select_category(conversation, selected.id)
        await self.client.send_message(
            chat_id,
            f"مخزن «{selected.name}» انتخاب شد. سؤال خود را بپرسید.",
        )

    async def _send_categories(self, chat_id: int, *, page: int, welcome: bool) -> None:
        categories = await self.categories.list_available_in_bale()
        if not categories:
            await self.client.send_message(chat_id, NO_AVAILABLE_CATEGORIES_TEXT)
            return
        await self.client.send_message(
            chat_id,
            WELCOME_TEXT if welcome else NO_CATEGORY_TEXT,
            reply_markup=category_keyboard(categories, page=page),
        )

    async def _send_main_menu(self, chat_id: int, *, welcome: bool) -> None:
        await self.client.send_message(
            chat_id,
            WELCOME_TEXT if welcome else "یکی از گزینه‌های زیر را انتخاب کنید.",
            reply_markup=main_menu_keyboard(),
        )

    async def _send_pending_upload_categories(self, conversation) -> None:
        pending_update_id = conversation.pending_upload_update_id
        if pending_update_id is None:
            self.repo.set_mode(conversation, "menu")
            await self._send_main_menu(conversation.chat_id, welcome=False)
            return
        categories = await self.categories.list_all()
        if not categories:
            conversation.pending_upload_update_id = None
            self.repo.set_mode(conversation, "menu")
            await self.client.send_message(
                conversation.chat_id,
                NO_AVAILABLE_CATEGORIES_TEXT,
                reply_markup=main_menu_keyboard(),
            )
            return
        await self.client.send_message(
            conversation.chat_id,
            "برای ادامه، دسته محتوای قبلی را انتخاب کنید:",
            reply_markup=upload_category_keyboard(
                categories,
                update_id=pending_update_id,
            ),
        )

    async def _resume_upload_with_category(
        self,
        conversation,
        *,
        pending_update_id: int,
        category_id: UUID,
        request_id: str | None,
    ) -> None:
        if conversation.pending_upload_update_id != pending_update_id:
            await self.client.send_message(conversation.chat_id, "این درخواست دیگر فعال نیست.")
            return
        pending = await self.repo.get_update(pending_update_id)
        category = await self.categories.get_by_id(category_id)
        if (
            pending is None
            or pending.status != "awaiting_category"
            or pending.chat_id != conversation.chat_id
            or category is None
        ):
            await self.client.send_message(
                conversation.chat_id,
                "این دسته یا درخواست دیگر در دسترس نیست.",
            )
            return
        pending.category_id_snapshot = category.id
        pending.classification_method = "user-confirmed-v1"
        pending.classification_needs_review = False
        self.repo.set_mode(conversation, "upload_processing")
        pending.intent_version_snapshot = conversation.intent_version
        try:
            await self._queue_update(pending, request_id=request_id)
        except HTTPException:
            self.repo.set_mode(conversation, "menu")
            conversation.pending_upload_update_id = None
            await self.session.commit()
            await self.client.send_message(
                conversation.chat_id,
                "ارسال محتوا به صف پردازش ناموفق بود. لطفاً دوباره تلاش کنید.",
                reply_markup=main_menu_keyboard(),
            )

    async def _queue_update(self, update, *, request_id: str | None) -> None:
        update.status = "queued"
        update.error_code = None
        update.error_message = None
        await self.session.commit()
        try:
            await publish_bale_update(update.update_id, request_id)
        except BalePublishError as exc:
            current = await self.repo.get_update(update.update_id)
            if current is not None and current.status == "queued":
                current.status = "failed"
                current.error_code = "queue_publish_failed"
                current.error_message = "The update could not be queued"
                await self.session.commit()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Bale message could not be queued",
            ) from exc

    async def _selected_category_is_available(self, category_id: UUID | None) -> bool:
        if category_id is None:
            return False
        categories = await self.categories.list_available_in_bale()
        return any(item.id == category_id for item in categories)

    @staticmethod
    def _update_values(payload: BaleUpdatePayload) -> dict:
        if payload.callback_query is not None:
            callback = payload.callback_query
            message = callback.message
            return {
                "update_id": payload.update_id,
                "kind": "callback_query",
                "status": "received",
                "chat_id": message.chat.id if message else None,
                "bale_user_id": callback.from_user.id,
                "message_id": message.message_id if message else None,
                "callback_query_id": callback.id,
                "callback_data": callback.data,
            }
        message = payload.message
        return {
            "update_id": payload.update_id,
            "kind": "message" if message else "unsupported",
            "status": "received",
            "chat_id": message.chat.id if message else None,
            "bale_user_id": message.from_user.id if message and message.from_user else None,
            "message_id": message.message_id if message else None,
            "text": message.text if message else None,
            "document_file_id": (
                message.document.file_id if message and message.document else None
            ),
            "document_file_unique_id": (
                message.document.file_unique_id if message and message.document else None
            ),
            "document_filename": (
                message.document.file_name if message and message.document else None
            ),
            "document_mime_type": (
                message.document.mime_type if message and message.document else None
            ),
            "document_size_bytes": (
                message.document.file_size if message and message.document else None
            ),
            "caption": message.caption if message else None,
        }
