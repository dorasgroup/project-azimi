import os
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.integrations.bale.upload_service import BaleTextUploadService
from app.models.bale import BaleConversation, BaleUpdate
from app.models.category import Category
from app.models.ingestion_job import IngestionJob
from app.models.upload import Upload
from app.models.user import User
from app.services.content_category_service import rank_content_category

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


class FakeBaleClient:
    def __init__(self) -> None:
        self.messages: list[dict] = []

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


def test_content_category_match_is_confident_only_for_unique_signal() -> None:
    marriage = Category(id=uuid4(), name="ازدواج")
    women = Category(id=uuid4(), name="کنشگری زنان")
    confident = rank_content_category(
        [marriage, women],
        "این متن درباره همسر، زوجین و مراسم عروسی است.",
    )
    uncertain = rank_content_category(
        [marriage, women],
        "این یک متن عمومی بدون نشانه موضوعی است.",
    )
    tie = rank_content_category(
        [marriage, women],
        "این متن هم درباره همسر و هم درباره بانوان است.",
    )
    assert confident.is_confident is True
    assert confident.category_id == marriage.id
    assert uncertain.is_confident is False
    assert tie.is_confident is False


@pytest.mark.asyncio
async def test_confident_bale_text_uses_normal_upload_and_is_idempotent(
    database,
    monkeypatch,
    tmp_path,
) -> None:
    user_id = uuid4()
    chat_id = 9_100_000_001
    update_id = 777_100_000_000_000_001
    fake_client = FakeBaleClient()

    async def publish_ok(_job_id, _request_id) -> None:
        return None

    monkeypatch.setattr(settings, "async_ingestion_enabled", True)
    monkeypatch.setattr(settings, "upload_storage_path", tmp_path)
    monkeypatch.setattr(settings, "bale_integration_user_id", user_id)
    monkeypatch.setattr("app.services.upload_service.publish_ingestion_job", publish_ok)

    async with database() as session:
        category = Category(name="ازدواج")
        session.add(
            User(
                id=user_id,
                email=f"bale-text-{uuid4()}@example.com",
                full_name="Bale text integration",
                hashed_password="not-a-login-credential",
                role="user",
                is_active=False,
            )
        )
        session.add(category)
        update = BaleUpdate(
            update_id=update_id,
            kind="message",
            status="processing",
            chat_id=chat_id,
            bale_user_id=91,
            message_id=92,
            text="این متن درباره ازدواج و همسر و زوجین است.",
            intent="upload",
        )
        session.add(update)
        await session.flush()
        conversation = BaleConversation(
            chat_id=chat_id,
            bale_user_id=91,
            chat_type="private",
            mode="upload_processing",
            intent_version=1,
            pending_upload_update_id=update_id,
            client_session_id=f"bale-{uuid4()}",
        )
        session.add(conversation)
        await session.commit()

        service = BaleTextUploadService(session, client=fake_client)
        outcome = await service.process(update, conversation, request_id="test-request")
        assert outcome == "upload_completed"
        assert update.upload_id is not None
        assert update.status == "completed"
        assert conversation.mode == "menu"

        upload = await session.get(Upload, update.upload_id)
        assert upload is not None
        assert upload.source == "bale"
        assert upload.source_key == f"bale:{update_id}"
        assert upload.original_filename.endswith(f"-{update_id}.txt")
        assert upload.classification_method == "lexical-v1"
        assert await session.scalar(
            select(func.count()).select_from(Upload).where(Upload.source_key == f"bale:{update_id}")
        ) == 1
        assert await session.scalar(
            select(func.count())
            .select_from(IngestionJob)
            .where(IngestionJob.upload_id == upload.id)
        ) == 1

        second = await service.process(update, conversation, request_id="test-request")
        assert second == "upload_completed"
        assert await session.scalar(
            select(func.count()).select_from(Upload).where(Upload.source_key == f"bale:{update_id}")
        ) == 1
        assert await session.scalar(
            select(func.count())
            .select_from(IngestionJob)
            .where(IngestionJob.upload_id == upload.id)
        ) == 1


@pytest.mark.asyncio
async def test_uncertain_bale_text_waits_for_category_without_upload(
    database,
    monkeypatch,
) -> None:
    chat_id = 9_100_000_002
    update_id = 777_100_000_000_000_002
    fake_client = FakeBaleClient()
    monkeypatch.setattr(settings, "bale_integration_user_id", uuid4())

    async with database() as session:
        session.add_all(
            [
                Category(name=f"موضوع عمومی الف {uuid4()}"),
                Category(name=f"موضوع عمومی ب {uuid4()}"),
            ]
        )
        update = BaleUpdate(
            update_id=update_id,
            kind="message",
            status="processing",
            chat_id=chat_id,
            bale_user_id=93,
            message_id=94,
            text="متنی که هیچ اشاره‌ای به نام یا کلیدواژه دسته‌ها ندارد.",
            intent="upload",
        )
        session.add(update)
        await session.flush()
        conversation = BaleConversation(
            chat_id=chat_id,
            bale_user_id=93,
            chat_type="private",
            mode="upload_processing",
            intent_version=1,
            pending_upload_update_id=update_id,
            client_session_id=f"bale-{uuid4()}",
        )
        session.add(conversation)
        await session.commit()

        outcome = await BaleTextUploadService(session, client=fake_client).process(
            update,
            conversation,
            request_id="test-request",
        )
        assert outcome == "awaiting_category"
        assert update.status == "awaiting_category"
        assert update.upload_id is None
        assert update.classification_needs_review is True
        assert conversation.mode == "upload_category"
        assert conversation.pending_upload_update_id == update_id
        assert await session.scalar(
            select(func.count()).select_from(Upload).where(Upload.source_key == f"bale:{update_id}")
        ) == 0
        assert fake_client.messages[-1]["reply_markup"]["inline_keyboard"]
