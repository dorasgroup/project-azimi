import os
from io import BytesIO
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from docx import Document
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.integrations.bale.client import BaleClient, BaleFileTooLargeError
from app.integrations.bale.file_content import (
    BaleContentValidationError,
    prepare_bale_document,
)
from app.integrations.bale.repository import BaleRepository
from app.integrations.bale.schemas import BaleUpdatePayload
from app.integrations.bale.service import BaleWebhookService
from app.integrations.bale.upload_service import BaleContentUploadService
from app.models.bale import BaleConversation, BaleUpdate
from app.models.category import Category
from app.models.ingestion_job import IngestionJob
from app.models.upload import Upload
from app.models.user import User

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


class FakeBaleFileClient:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.messages: list[dict] = []
        self.download_calls: list[tuple[str, int]] = []

    async def download_file(self, file_id: str, *, max_size_bytes: int) -> bytes:
        self.download_calls.append((file_id, max_size_bytes))
        return self.content

    async def send_message(self, chat_id: int, text: str, **kwargs) -> dict:
        self.messages.append({"chat_id": chat_id, "text": text, **kwargs})
        return {"message_id": len(self.messages)}


@pytest_asyncio.fixture
async def database():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is required")
    engine = create_async_engine(TEST_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_bale_client_downloads_only_bounded_safe_file_path(monkeypatch) -> None:
    token = "private-file-token"
    monkeypatch.setattr(settings, "bale_bot_token_file", None)
    monkeypatch.setattr(settings, "bale_bot_token", SecretStr(token))
    requests: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": {
                        "file_path": "documents/report.txt",
                        "file_size": 4,
                    },
                },
            )
        return httpx.Response(200, content=b"test", headers={"content-length": "4"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        content = await BaleClient(http_client).download_file("file-id", max_size_bytes=8)

    assert content == b"test"
    assert requests[0][0] == "POST"
    assert requests[0][1].endswith(f"/bot{token}/getFile")
    assert requests[1][0] == "GET"
    assert requests[1][1].endswith(f"/file/bot{token}/documents/report.txt")


@pytest.mark.asyncio
async def test_bale_client_rejects_oversized_file_before_download(monkeypatch) -> None:
    monkeypatch.setattr(settings, "bale_bot_token_file", None)
    monkeypatch.setattr(settings, "bale_bot_token", SecretStr("private-file-token"))
    request_count = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {"file_path": "documents/large.pdf", "file_size": 9},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        with pytest.raises(BaleFileTooLargeError):
            await BaleClient(http_client).download_file("file-id", max_size_bytes=8)
    assert request_count == 1


def test_docx_text_is_extracted_for_classification() -> None:
    buffer = BytesIO()
    document = Document()
    document.add_paragraph("این فایل درباره ازدواج، همسر و زوجین است.")
    document.save(buffer)

    prepared = prepare_bale_document(
        filename="گزارش.docx",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        reported_size=len(buffer.getvalue()),
        content=buffer.getvalue(),
        caption=None,
    )

    assert prepared.filename == "گزارش.docx"
    assert "ازدواج" in prepared.classification_text
    assert "زوجین" in prepared.classification_text


def test_disguised_pdf_is_rejected() -> None:
    with pytest.raises(BaleContentValidationError):
        prepare_bale_document(
            filename="unsafe.pdf",
            mime_type="application/pdf",
            reported_size=12,
            content=b"not-a-pdf!!",
            caption=None,
        )


@pytest.mark.asyncio
async def test_webhook_queues_document_while_conversation_is_in_upload_mode(
    database,
    monkeypatch,
) -> None:
    chat_id = 9_200_000_001
    published: list[int] = []

    async def publish_ok(update_id: int, _request_id: str | None) -> None:
        published.append(update_id)

    monkeypatch.setattr("app.integrations.bale.service.publish_bale_update", publish_ok)
    payload = BaleUpdatePayload.model_validate(
        {
            "update_id": 777_200_000_000_000_001,
            "message": {
                "message_id": 100,
                "chat": {"id": chat_id, "type": "private"},
                "from": {"id": 200, "first_name": "کاربر"},
                "document": {
                    "file_id": "downloadable-id",
                    "file_unique_id": "stable-id",
                    "file_name": "گزارش.pdf",
                    "mime_type": "application/pdf",
                    "file_size": 1234,
                },
            },
        }
    )

    async with database() as session:
        repo = BaleRepository(session)
        conversation = await repo.get_or_create_conversation(
            chat_id=chat_id,
            bale_user_id=200,
            chat_type="private",
        )
        repo.set_mode(conversation, "upload")
        await session.commit()

        result = await BaleWebhookService(session).handle(payload, request_id="file-test")
        update = await repo.get_update(payload.update_id)

        assert result.accepted is True
        assert update is not None
        assert update.status == "queued"
        assert update.intent == "upload"
        assert update.document_file_id == "downloadable-id"
        assert conversation.mode == "upload_processing"
        assert published == [payload.update_id]


@pytest.mark.asyncio
async def test_confident_bale_txt_file_uses_normal_upload_pipeline(
    database,
    monkeypatch,
    tmp_path,
) -> None:
    user_id = uuid4()
    chat_id = 9_200_000_002
    update_id = 777_200_000_000_000_002
    category_name = f"ازدواج {uuid4().hex}"
    content = f"این فایل درباره {category_name}، همسر و زوجین است.".encode()
    fake_client = FakeBaleFileClient(content)

    async def publish_ok(_job_id, _request_id) -> None:
        return None

    monkeypatch.setattr(settings, "async_ingestion_enabled", True)
    monkeypatch.setattr(settings, "upload_storage_path", tmp_path)
    monkeypatch.setattr(settings, "bale_integration_user_id", user_id)
    monkeypatch.setattr("app.services.upload_service.publish_ingestion_job", publish_ok)

    async with database() as session:
        category = Category(name=category_name)
        session.add(
            User(
                id=user_id,
                email=f"bale-file-{uuid4()}@example.com",
                full_name="Bale file integration",
                hashed_password="not-a-login-credential",
                role="user",
                is_active=False,
            )
        )
        session.add_all([category, Category(name=f"کنشگری زنان {uuid4().hex}")])
        update = BaleUpdate(
            update_id=update_id,
            kind="message",
            status="processing",
            chat_id=chat_id,
            bale_user_id=201,
            message_id=101,
            intent="upload",
            document_file_id="downloadable-id",
            document_file_unique_id="stable-id",
            document_filename="گزارش ازدواج.txt",
            document_mime_type="text/plain",
            document_size_bytes=len(content),
        )
        session.add(update)
        await session.flush()
        conversation = BaleConversation(
            chat_id=chat_id,
            bale_user_id=201,
            chat_type="private",
            mode="upload_processing",
            intent_version=1,
            pending_upload_update_id=update_id,
            client_session_id=f"bale-{uuid4()}",
        )
        session.add(conversation)
        await session.commit()

        outcome = await BaleContentUploadService(session, client=fake_client).process(
            update,
            conversation,
            request_id="file-test",
        )

        assert outcome == "upload_completed"
        assert update.status == "completed"
        assert update.upload_id is not None
        upload = await session.get(Upload, update.upload_id)
        assert upload is not None
        assert upload.original_filename == "گزارش ازدواج.txt"
        assert upload.source == "bale"
        await session.refresh(upload, attribute_names=["categories"])
        assert {item.name for item in upload.categories} == {category_name}
        assert await session.scalar(
            select(func.count())
            .select_from(IngestionJob)
            .where(IngestionJob.upload_id == upload.id)
        ) == 1
        assert fake_client.download_calls == [
            ("downloadable-id", settings.bale_file_upload_max_bytes)
        ]


@pytest.mark.asyncio
async def test_unsupported_bale_file_is_rejected_without_download(
    database,
    monkeypatch,
) -> None:
    update_id = 777_200_000_000_000_003
    chat_id = 9_200_000_003
    fake_client = FakeBaleFileClient(b"not-used")
    monkeypatch.setattr(settings, "bale_integration_user_id", uuid4())

    async with database() as session:
        update = BaleUpdate(
            update_id=update_id,
            kind="message",
            status="processing",
            chat_id=chat_id,
            bale_user_id=202,
            message_id=102,
            intent="upload",
            document_file_id="downloadable-id",
            document_filename="malware.exe",
            document_mime_type="application/octet-stream",
            document_size_bytes=8,
        )
        session.add(update)
        await session.flush()
        conversation = BaleConversation(
            chat_id=chat_id,
            bale_user_id=202,
            chat_type="private",
            mode="upload_processing",
            intent_version=1,
            pending_upload_update_id=update_id,
            client_session_id=f"bale-{uuid4()}",
        )
        session.add(conversation)
        await session.commit()

        outcome = await BaleContentUploadService(session, client=fake_client).process(
            update,
            conversation,
            request_id="file-test",
        )

        assert outcome == "rejected"
        assert update.status == "rejected"
        assert update.error_code == "invalid_file_upload"
        assert fake_client.download_calls == []
        assert await session.scalar(
            select(func.count()).select_from(Upload).where(Upload.source_key == f"bale:{update_id}")
        ) == 0
