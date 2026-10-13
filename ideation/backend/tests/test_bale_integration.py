import os
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.integrations.bale.client import BaleAPIError, BaleClient
from app.integrations.bale.keyboard import (
    CATEGORY_PAGE_SIZE,
    MENU_REPOSITORIES_CALLBACK,
    MENU_UPLOAD_CALLBACK,
    category_callback,
    category_keyboard,
    main_menu_keyboard,
    parse_categories_page_callback,
    parse_category_callback,
    parse_upload_categories_page_callback,
    parse_upload_category_callback,
    upload_categories_page_callback,
    upload_category_callback,
    upload_category_keyboard,
)
from app.integrations.bale.repository import BaleRepository
from app.integrations.bale.schemas import BaleUpdatePayload
from app.integrations.bale.service import require_bale_webhook
from app.integrations.bale.tasks import split_bale_message
from app.integrations.bale.worker_entrypoint import preload_bale_bot_token
from app.main import app
from app.models import Upload, User
from app.models.category import Category
from app.models.upload import upload_categories
from app.repositories.category_repository import CategoryRepository

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


def test_bale_integration_is_hidden_while_disabled(monkeypatch) -> None:
    monkeypatch.setattr(settings, "bale_integration_enabled", False)
    with pytest.raises(HTTPException) as exc:
        require_bale_webhook("anything")
    assert exc.value.status_code == 404


def test_bale_webhook_route_is_hidden_while_disabled(monkeypatch) -> None:
    monkeypatch.setattr(settings, "bale_integration_enabled", False)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/integrations/bale/webhook?key=irrelevant",
            json={"update_id": 1},
        )
    assert response.status_code == 404


def test_bale_webhook_rejects_missing_and_invalid_secret(monkeypatch) -> None:
    monkeypatch.setattr(settings, "bale_integration_enabled", True)
    monkeypatch.setattr(settings, "bale_webhook_secret_file", None)
    monkeypatch.setattr(settings, "bale_webhook_secret", None)
    with pytest.raises(HTTPException) as missing:
        require_bale_webhook("anything")
    assert missing.value.status_code == 503

    monkeypatch.setattr(settings, "bale_webhook_secret", SecretStr("expected"))
    with pytest.raises(HTTPException) as invalid:
        require_bale_webhook("wrong")
    assert invalid.value.status_code == 401
    require_bale_webhook("expected")


def test_bale_update_parses_message_and_callback_aliases() -> None:
    message = BaleUpdatePayload.model_validate(
        {
            "update_id": 10,
            "message": {
                "message_id": 20,
                "chat": {"id": 30, "type": "private"},
                "from": {"id": 40, "is_bot": False, "first_name": "کاربر"},
                "text": "سلام",
            },
        }
    )
    assert message.message is not None
    assert message.message.from_user is not None
    assert message.message.from_user.id == 40

    callback = BaleUpdatePayload.model_validate(
        {
            "update_id": 11,
            "callback_query": {
                "id": "callback-1",
                "from": {"id": 40, "first_name": "کاربر"},
                "data": "cats:0",
            },
        }
    )
    assert callback.callback_query is not None
    assert callback.callback_query.from_user.id == 40


def test_bale_update_parses_document_metadata_without_file_content() -> None:
    payload = BaleUpdatePayload.model_validate(
        {
            "update_id": 12,
            "message": {
                "message_id": 21,
                "chat": {"id": 30, "type": "private"},
                "from": {"id": 40, "first_name": "کاربر"},
                "document": {
                    "file_id": "downloadable-id",
                    "file_unique_id": "stable-id",
                    "file_name": "گزارش.pdf",
                    "mime_type": "application/pdf",
                    "file_size": 1234,
                },
                "caption": "محتوای مربوط به ازدواج",
            },
        }
    )
    assert payload.message is not None
    assert payload.message.document is not None
    assert payload.message.document.file_id == "downloadable-id"
    assert payload.message.document.file_name == "گزارش.pdf"
    assert payload.message.caption == "محتوای مربوط به ازدواج"


def test_main_menu_contains_exactly_upload_and_repository_actions() -> None:
    rows = main_menu_keyboard()["inline_keyboard"]
    assert len(rows) == 1
    assert [button["text"] for button in rows[0]] == ["بارگذاری محتوا", "انتخاب مخزن"]
    assert [button["callback_data"] for button in rows[0]] == [
        MENU_UPLOAD_CALLBACK,
        MENU_REPOSITORIES_CALLBACK,
    ]
    assert all(len(button["callback_data"].encode("utf-8")) <= 64 for button in rows[0])


def test_category_keyboard_is_paginated_and_callbacks_fit_bale_limit() -> None:
    categories = [Category(id=uuid4(), name=f"مخزن {index}") for index in range(10)]
    first_page = category_keyboard(categories, page=0)["inline_keyboard"]
    second_page = category_keyboard(categories, page=1)["inline_keyboard"]

    assert len(first_page) == CATEGORY_PAGE_SIZE + 1
    assert len(second_page) == 3
    callback = first_page[0][0]["callback_data"]
    assert len(callback.encode("utf-8")) <= 64
    assert parse_category_callback(callback) == categories[0].id
    assert parse_categories_page_callback("cats:2") == 2
    assert parse_category_callback("cat:not-a-uuid") is None
    assert category_callback(categories[0].id).startswith("cat:")


def test_upload_category_keyboard_has_distinct_bounded_callbacks() -> None:
    categories = [Category(id=uuid4(), name=f"دسته {index}") for index in range(10)]
    update_id = 777_000_000_000_000_001
    first_page = upload_category_keyboard(
        categories,
        update_id=update_id,
    )["inline_keyboard"]
    callback = first_page[0][0]["callback_data"]
    assert len(callback.encode("utf-8")) <= 64
    assert parse_upload_category_callback(callback) == (update_id, categories[0].id)
    assert parse_upload_categories_page_callback(
        upload_categories_page_callback(update_id, 2)
    ) == (update_id, 2)
    assert upload_category_callback(update_id, categories[0].id).startswith("upcat:")


def test_long_answers_are_split_below_bale_limit() -> None:
    chunks = split_bale_message("الف " * 5000)
    assert len(chunks) > 1
    assert all(0 < len(chunk) <= 3900 for chunk in chunks)
    assert "".join(chunks).replace(" ", "") == ("الف " * 5000).strip().replace(" ", "")


def test_worker_preloads_root_owned_bale_token(monkeypatch, tmp_path) -> None:
    token_file = tmp_path / "bale-token"
    token_file.write_text("worker-only-token\n", encoding="utf-8")
    monkeypatch.setattr(settings, "bale_bot_token", None)
    monkeypatch.setattr(settings, "bale_bot_token_file", token_file)

    preload_bale_bot_token()

    assert settings.bale_bot_token_file is None
    assert settings.bale_bot_token is not None
    assert settings.bale_bot_token.get_secret_value() == "worker-only-token"


@pytest.mark.asyncio
async def test_bale_client_sends_inline_keyboard_without_leaking_token(monkeypatch) -> None:
    token = "private-test-token"
    monkeypatch.setattr(settings, "bale_bot_token_file", None)
    monkeypatch.setattr(settings, "bale_bot_token", SecretStr(token))
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["payload"] = request.content
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        result = await BaleClient(http_client).send_message(
            123,
            "سلام",
            reply_markup={"inline_keyboard": [[{"text": "مخزن", "callback_data": "cats:0"}]]},
        )

    assert result["message_id"] == 1
    assert captured["path"].endswith(f"/bot{token}/sendMessage")
    assert b"inline_keyboard" in captured["payload"]


@pytest.mark.asyncio
async def test_bale_client_can_edit_progress_message(monkeypatch) -> None:
    token = "private-test-token"
    monkeypatch.setattr(settings, "bale_bot_token_file", None)
    monkeypatch.setattr(settings, "bale_bot_token", SecretStr(token))
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["payload"] = request.content
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 9}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        await BaleClient(http_client).edit_message_text(123, 9, "پاسخ تدریجی")

    assert captured["path"].endswith(f"/bot{token}/editMessageText")
    assert b'"message_id":9' in captured["payload"]


@pytest.mark.asyncio
async def test_bale_client_redacts_token_from_errors(monkeypatch) -> None:
    token = "must-never-appear"
    monkeypatch.setattr(settings, "bale_bot_token_file", None)
    monkeypatch.setattr(settings, "bale_bot_token", SecretStr(token))

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"ok": False, "error_code": 401})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        with pytest.raises(BaleAPIError) as exc:
            await BaleClient(http_client).get_me()
    assert token not in str(exc.value)


@pytest_asyncio.fixture
async def database():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is required")
    engine = create_async_engine(TEST_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_bale_updates_are_idempotent_and_categories_are_opt_in(database) -> None:
    user_id = uuid4()
    category = Category(name=f"bale-visible-{uuid4()}", available_in_bale=True)
    empty_category = Category(name=f"bale-empty-{uuid4()}", available_in_bale=True)
    hidden = Category(name=f"bale-hidden-{uuid4()}", available_in_bale=False)
    upload = Upload(
        uploaded_by=user_id,
        original_filename="bale.txt",
        stored_filename=f"{uuid4()}.txt",
        storage_path="/tmp/bale.txt",
        file_extension="txt",
        mime_type="text/plain",
        file_size_bytes=10,
        sha256_hash=uuid4().hex + uuid4().hex,
        status="indexed",
        dify_document_id=uuid4(),
    )
    async with database() as session:
        session.add(
            User(
                id=user_id,
                email=f"bale-test-{uuid4()}@example.com",
                full_name="Bale test user",
                hashed_password="not-a-login-credential",
                role="user",
                is_active=True,
            )
        )
        session.add_all([category, empty_category, hidden, upload])
        await session.flush()
        await session.execute(
            upload_categories.insert().values(upload_id=upload.id, category_id=category.id)
        )
        await session.commit()

        available = await CategoryRepository(session).list_available_in_bale()
        assert category.id in {item.id for item in available}
        assert empty_category.id in {item.id for item in available}
        assert hidden.id not in {item.id for item in available}

        values = {
            "update_id": 9_000_000_000 + (uuid4().int % 1_000_000_000),
            "kind": "message",
            "status": "received",
            "chat_id": 123,
            "bale_user_id": 456,
            "message_id": 789,
            "text": "سلام",
        }
        first, first_duplicate = await BaleRepository(session).claim_update(values)
        await session.commit()
        second, second_duplicate = await BaleRepository(session).claim_update(values)
        assert first.update_id == second.update_id
        assert first_duplicate is False
        assert second_duplicate is True

        conversation = await BaleRepository(session).get_or_create_conversation(
            chat_id=123,
            bale_user_id=456,
            chat_type="private",
        )
        assert conversation.mode == "menu"
        original_version = conversation.intent_version
        BaleRepository.set_mode(conversation, "select_repository")
        assert conversation.mode == "select_repository"
        assert conversation.intent_version == original_version + 1
        BaleRepository.select_category(conversation, category.id)
        assert conversation.mode == "chat"
        assert conversation.selected_category_id == category.id
        assert conversation.intent_version == original_version + 2
