from dataclasses import dataclass
from datetime import UTC
from io import BytesIO

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import Headers

from app.core.config import settings
from app.integrations.bale.client import BaleClient, BaleFileTooLargeError
from app.integrations.bale.file_content import (
    BaleContentValidationError,
    prepare_bale_document,
    validate_bale_document_metadata,
)
from app.integrations.bale.keyboard import main_menu_keyboard, upload_category_keyboard
from app.integrations.bale.repository import BaleRepository
from app.models.bale import BaleConversation, BaleUpdate
from app.repositories.category_repository import CategoryRepository
from app.services.content_category_service import classify_content_category
from app.services.upload_service import UploadOrigin, UploadService

CLASSIFIER_VERSION = "lexical-v1"


@dataclass(frozen=True)
class PreparedUpload:
    file: UploadFile
    classification_text: str
    content_label: str


class BaleContentUploadService:
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

    async def process(
        self,
        update: BaleUpdate,
        conversation: BaleConversation,
        *,
        request_id: str | None,
    ) -> str:
        if settings.bale_integration_user_id is None:
            raise RuntimeError("Bale integration user is not configured")
        try:
            prepared = await self._prepare_upload(update)
        except BaleFileTooLargeError:
            return await self._reject(
                update,
                conversation,
                f"حجم فایل بیشتر از سقف مجاز {settings.bale_file_upload_max_mb} مگابایت است.",
            )
        except BaleContentValidationError as exc:
            return await self._reject(update, conversation, exc.message)

        if update.category_id_snapshot is None:
            match = await classify_content_category(
                self.session,
                prepared.classification_text,
            )
            update.classification_method = CLASSIFIER_VERSION
            update.classification_score = float(match.score)
            if not match.is_confident:
                return await self._await_category(
                    update,
                    conversation,
                    "دسته این محتوا با اطمینان کافی تشخیص داده نشد. "
                    "برای جلوگیری از ثبت در مخزن اشتباه، دسته را انتخاب کنید:",
                )
            update.category_id_snapshot = match.category_id
            update.classification_needs_review = False
            await self.session.commit()

        category = await self.categories.get_by_id(update.category_id_snapshot)
        if category is None:
            update.category_id_snapshot = None
            return await self._await_category(
                update,
                conversation,
                "دسته قبلی دیگر وجود ندارد. یک دسته جدید انتخاب کنید:",
            )

        result = await UploadService(self.session).create_upload(
            settings.bale_integration_user_id,
            prepared.file,
            category_ids=[category.id],
            request_id=request_id,
            origin=UploadOrigin(
                source="bale",
                source_key=f"bale:{update.update_id}",
                classification_method=(update.classification_method or "user-confirmed-v1"),
                classification_version=CLASSIFIER_VERSION,
                classification_score=update.classification_score,
                classification_needs_review=update.classification_needs_review,
                bale_update_id=update.update_id,
            ),
        )
        filename = prepared.file.filename or "content"
        receipt = (
            f"{prepared.content_label} شما با نام «{filename}» در دسته «{category.name}» "
            "ذخیره شد و برای پردازش در Dify در صف قرار گرفت."
        )
        if result.status == "enqueue_failed":
            receipt = (
                f"{prepared.content_label} شما با نام «{filename}» در دسته «{category.name}» "
                "ذخیره شد، اما ارسال به صف Dify نیاز به تلاش مجدد دارد."
            )
        update.answer_text = receipt
        update.status = "completed"
        update.error_code = None
        update.error_message = None
        conversation.pending_upload_update_id = None
        self.repo.set_mode(conversation, "menu")
        await self.session.commit()
        await self.client.send_message(
            update.chat_id,
            receipt,
            reply_markup=main_menu_keyboard(),
            reply_to_message_id=update.message_id,
        )
        return "upload_completed"

    async def _prepare_upload(self, update: BaleUpdate) -> PreparedUpload:
        if update.document_file_id:
            validate_bale_document_metadata(
                filename=update.document_filename,
                mime_type=update.document_mime_type,
                reported_size=update.document_size_bytes,
            )
            content = await self.client.download_file(
                update.document_file_id,
                max_size_bytes=settings.bale_file_upload_max_bytes,
            )
            document = prepare_bale_document(
                filename=update.document_filename,
                mime_type=update.document_mime_type,
                reported_size=update.document_size_bytes,
                content=content,
                caption=update.caption,
            )
            return PreparedUpload(
                file=UploadFile(
                    file=BytesIO(document.content),
                    filename=document.filename,
                    headers=Headers({"content-type": document.mime_type}),
                ),
                classification_text=document.classification_text,
                content_label="فایل",
            )

        text = (update.text or "").strip()
        if not text:
            raise BaleContentValidationError("متن ارسالی خالی است.")
        if len(text) > settings.bale_text_upload_max_chars:
            raise BaleContentValidationError(
                f"متن ارسالی بیشتر از {settings.bale_text_upload_max_chars:,} نویسه است."
            )
        created_at = update.created_at.astimezone(UTC)
        filename = f"bale-text-{created_at:%Y%m%d}-{update.update_id}.txt"
        return PreparedUpload(
            file=UploadFile(
                file=BytesIO(text.encode("utf-8")),
                filename=filename,
                headers=Headers({"content-type": "text/plain; charset=utf-8"}),
            ),
            classification_text=text,
            content_label="متن",
        )

    async def _await_category(
        self,
        update: BaleUpdate,
        conversation: BaleConversation,
        message: str,
    ) -> str:
        update.classification_needs_review = True
        update.status = "awaiting_category"
        conversation.pending_upload_update_id = update.update_id
        self.repo.set_mode(conversation, "upload_category")
        categories = await self.categories.list_all()
        if not categories:
            return await self._reject(
                update,
                conversation,
                "هیچ دسته‌ای برای ثبت محتوا در سامانه وجود ندارد.",
            )
        await self.session.commit()
        await self.client.send_message(
            update.chat_id,
            message,
            reply_markup=upload_category_keyboard(
                categories,
                update_id=update.update_id,
            ),
            reply_to_message_id=update.message_id,
        )
        return "awaiting_category"

    async def _reject(
        self,
        update: BaleUpdate,
        conversation: BaleConversation,
        message: str,
    ) -> str:
        update.status = "rejected"
        update.error_code = (
            "invalid_file_upload" if update.document_file_id else "invalid_text_upload"
        )
        update.error_message = message
        conversation.pending_upload_update_id = None
        self.repo.set_mode(conversation, "menu")
        await self.session.commit()
        await self.client.send_message(
            update.chat_id,
            message,
            reply_markup=main_menu_keyboard(),
            reply_to_message_id=update.message_id,
        )
        return "rejected"


# Kept as a compatibility alias for callers and tests introduced in phase 2.
BaleTextUploadService = BaleContentUploadService
