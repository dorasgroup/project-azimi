import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings
from app.core.logging import log_event
from app.observability.metrics import (
    bounded_exception_type,
    observe_dependency,
    record_fallback,
)

logger = logging.getLogger(__name__)


class DifyService:
    def __init__(self) -> None:
        self.base_url = str(settings.dify_base_url).rstrip("/")
        self.weaviate_url = str(settings.dify_weaviate_url).rstrip("/")

    async def send_chat_message(
        self,
        query: str,
        user: str,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "inputs": {},
            "query": query,
            "response_mode": "blocking",
            "conversation_id": conversation_id or "",
            "user": user,
        }
        async with observe_dependency("dify", "chat"):
            async with httpx.AsyncClient(timeout=120) as client:
                response = await client.post(
                    f"{self.base_url}/chat-messages",
                    headers={"Authorization": f"Bearer {settings.dify_chat_api_key}"},
                    json=payload,
                )
                response.raise_for_status()
                return response.json()

    async def send_chat_message_streaming(
        self,
        query: str,
        user: str,
        conversation_id: str | None = None,
        on_answer_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "inputs": {},
            "query": query,
            "response_mode": "streaming",
            "conversation_id": conversation_id or "",
            "user": user,
        }
        answer_parts: list[str] = []
        result: dict[str, Any] = {}
        async with observe_dependency("dify", "chat"):
            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/chat-messages",
                    headers={"Authorization": f"Bearer {settings.dify_chat_api_key}"},
                    json=payload,
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        raw_event = line.removeprefix("data:").strip()
                        if not raw_event:
                            continue
                        event = json.loads(raw_event)
                        event_name = event.get("event")
                        if event_name == "error":
                            # Dify can report a failed run inside an HTTP-200 stream.
                            # Do not accept partial/empty output as a normal completion.
                            error_status = event.get("status")
                            if not isinstance(error_status, int) or not 400 <= error_status <= 599:
                                error_status = 502
                            raise httpx.HTTPStatusError(
                                "Dify stream failed",
                                request=response.request,
                                response=httpx.Response(
                                    error_status,
                                    json={"message": event.get("message") or "Dify stream failed"},
                                ),
                            )
                        for key in ("task_id", "message_id", "id", "conversation_id"):
                            if event.get(key):
                                result[key] = event[key]
                        if event_name in {"message", "agent_message"}:
                            delta = str(event.get("answer") or "")
                            if delta:
                                answer_parts.append(delta)
                                if on_answer_progress is not None:
                                    await on_answer_progress("".join(answer_parts))
                        if event_name == "message_end":
                            result.update(event)
        result["answer"] = "".join(answer_parts)
        return result

    async def list_datasets(self) -> dict[str, Any]:
        async with observe_dependency("dify", "health"):
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(
                    f"{self.base_url}/datasets",
                    headers={"Authorization": f"Bearer {settings.dify_dataset_api_key}"},
                    params={"page": 1, "limit": 20},
                )
                response.raise_for_status()
                return response.json()

    async def _retrieve_via_dify_api(
        self,
        query: str,
        limit: int = 5,
        document_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Use Dify's semantic retrieval API when direct Weaviate access is unavailable."""
        # Fetch a wider candidate set before applying the repository filter.
        # Otherwise a relevant selected document can be excluded by results
        # belonging to another repository in the shared Dify dataset.
        retrieval_limit = max(limit, 20) if document_ids else limit
        payload = {
            "query": query,
            "retrieval_model": {
                "search_method": "semantic_search",
                "top_k": retrieval_limit,
                "reranking_enable": False,
                "score_threshold_enabled": False,
                "score_threshold": 0.0,
            },
        }
        try:
            async with observe_dependency("dify", "retrieve"):
                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.post(
                        f"{self.base_url}/datasets/{settings.dify_dataset_id}/retrieve",
                        headers={
                            "Authorization": f"Bearer {settings.dify_dataset_api_key}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                    )
                    response.raise_for_status()
                    data = response.json()
        except Exception as exc:
            log_event(
                logger,
                logging.WARNING,
                "dependency_request_failed",
                dependency="dify",
                operation="retrieve",
                outcome="fallback_failed",
                error_type=bounded_exception_type(exc),
            )
            return []

        records = data.get("records") or []
        result = []
        for record in records:
            segment = record.get("segment") or record
            document = segment.get("document")
            document_id = segment.get("document_id") or ""
            source = None
            if isinstance(document, dict):
                document_id = document_id or document.get("id", "")
                source = document.get("name")
            result.append(
                {
                    "text": segment.get("content") or segment.get("text") or "",
                    "source": source,
                    "document_id": document_id,
                    "doc_id": segment.get("id") or "",
                    "_additional": {"score": record.get("score") or segment.get("score") or 0},
                }
            )
        filtered = [item for item in result if item.get("text")]
        if document_ids:
            allowed_document_ids = {str(document_id) for document_id in document_ids}
            filtered = [
                item for item in filtered if str(item.get("document_id")) in allowed_document_ids
            ]
        return filtered[:limit]

    async def retrieve_knowledge_chunks(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        class_name = f"Vector_index_{str(settings.dify_dataset_id).replace('-', '_')}_Node"
        graphql_query = """
        query SearchKnowledge($query: String!) {
          Get {
            %s(bm25: {query: $query}, limit: %d) {
              text
              source
              document_id
              doc_id
              _additional {
                score
              }
            }
          }
        }
        """ % (class_name, limit)  # noqa: UP031 - GraphQL braces make this safer.
        headers = {"Content-Type": "application/json"}
        if settings.dify_weaviate_api_key:
            headers["Authorization"] = f"Bearer {settings.dify_weaviate_api_key}"

        try:
            async with observe_dependency("weaviate", "retrieve"):
                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.post(
                        f"{self.weaviate_url}/v1/graphql",
                        headers=headers,
                        json={"query": graphql_query, "variables": {"query": query}},
                    )
                    response.raise_for_status()
                    payload = response.json()

            if payload.get("errors"):
                record_fallback("weaviate", "dify", "response_error")
                log_event(
                    logger,
                    logging.WARNING,
                    "dependency_fallback",
                    dependency="weaviate",
                    operation="retrieve",
                    outcome="response_error",
                )
                return await self._retrieve_via_dify_api(query, limit)

            records = ((payload.get("data") or {}).get("Get") or {}).get(class_name) or []
            results = [
                record for record in records if isinstance(record, dict) and record.get("text")
            ]
            if results:
                return results
            record_fallback("weaviate", "dify", "empty")
            return await self._retrieve_via_dify_api(query, limit)
        except (httpx.HTTPStatusError, httpx.ConnectError, httpx.TimeoutException) as exc:
            reason = bounded_exception_type(exc)
            reason = "http_error" if reason == "http_status" else reason
            record_fallback("weaviate", "dify", reason)
            log_event(
                logger,
                logging.WARNING,
                "dependency_fallback",
                dependency="weaviate",
                operation="retrieve",
                outcome=reason,
                error_type=reason,
            )
            return await self._retrieve_via_dify_api(query, limit)

    async def retrieve_chunks_filtered(
        self,
        query: str,
        document_ids: list[str],
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        if not document_ids:
            return []

        class_name = f"Vector_index_{str(settings.dify_dataset_id).replace('-', '_')}_Node"

        operands = []
        for doc_id in document_ids:
            operands.append(f'{{path: ["document_id"], operator: Equal, valueString: "{doc_id}"}}')

        where_clause = ""
        if len(operands) == 1:
            where_clause = f"where: {operands[0]}"
        elif len(operands) > 1:
            where_clause = f"where: {{operator: Or, operands: [{', '.join(operands)}]}}"

        graphql_query = """
        query SearchKnowledge($query: String!) {
          Get {
            %s(
              bm25: {query: $query},
              limit: %d,
              %s
            ) {
              text
              source
              document_id
              doc_id
              _additional {
                score
              }
            }
          }
        }
        """ % (class_name, limit, where_clause)  # noqa: UP031 - GraphQL braces.

        headers = {"Content-Type": "application/json"}
        if settings.dify_weaviate_api_key:
            headers["Authorization"] = f"Bearer {settings.dify_weaviate_api_key}"

        try:
            async with observe_dependency("weaviate", "retrieve"):
                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.post(
                        f"{self.weaviate_url}/v1/graphql",
                        headers=headers,
                        json={"query": graphql_query, "variables": {"query": query}},
                    )
                    response.raise_for_status()
                    payload = response.json()

            if payload.get("errors"):
                record_fallback("weaviate", "dify", "response_error")
                log_event(
                    logger,
                    logging.WARNING,
                    "dependency_fallback",
                    dependency="weaviate",
                    operation="retrieve",
                    outcome="response_error",
                )
                return await self._retrieve_via_dify_api(query, limit, document_ids)

            records = ((payload.get("data") or {}).get("Get") or {}).get(class_name) or []
            results = [
                record for record in records if isinstance(record, dict) and record.get("text")
            ]
            if results:
                return results
            record_fallback("weaviate", "dify", "empty")
            return await self._retrieve_via_dify_api(query, limit, document_ids)
        except (httpx.HTTPStatusError, httpx.ConnectError, httpx.TimeoutException) as exc:
            reason = bounded_exception_type(exc)
            reason = "http_error" if reason == "http_status" else reason
            record_fallback("weaviate", "dify", reason)
            log_event(
                logger,
                logging.WARNING,
                "dependency_fallback",
                dependency="weaviate",
                operation="retrieve",
                outcome=reason,
                error_type=reason,
            )
            return await self._retrieve_via_dify_api(query, limit, document_ids)

    async def create_document_by_file(
        self,
        file_path: Path,
        filename: str,
        mime_type: str,
    ) -> dict[str, Any]:
        payload = {
            "indexing_technique": "high_quality",
            "process_rule": {
                "mode": "automatic",
            },
        }

        async with observe_dependency("dify", "create_document"):
            async with httpx.AsyncClient(timeout=180) as client:
                with file_path.open("rb") as file_buffer:
                    response = await client.post(
                        f"{self.base_url}/datasets/{settings.dify_dataset_id}/document/create-by-file",
                        headers={"Authorization": f"Bearer {settings.dify_dataset_api_key}"},
                        data={"data": json.dumps(payload, ensure_ascii=False)},
                        files={"file": (filename, file_buffer, mime_type)},
                    )
                response.raise_for_status()
                return response.json()

    async def delete_document(self, document_id: str) -> None:
        async with observe_dependency("dify", "delete_document"):
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.delete(
                    f"{self.base_url}/datasets/{settings.dify_dataset_id}/documents/{document_id}",
                    headers={"Authorization": f"Bearer {settings.dify_dataset_api_key}"},
                )
                response.raise_for_status()

    async def get_document(self, document_id: str) -> dict[str, Any] | None:
        async with observe_dependency("dify", "get_document"):
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(
                    (
                        f"{self.base_url}/datasets/{settings.dify_dataset_id}"
                        f"/documents/{document_id}"
                    ),
                    headers={"Authorization": f"Bearer {settings.dify_dataset_api_key}"},
                )
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                return response.json()

    async def list_documents(self) -> dict[str, Any]:
        async with observe_dependency("dify", "list_documents"):
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(
                    f"{self.base_url}/datasets/{settings.dify_dataset_id}/documents",
                    headers={"Authorization": f"Bearer {settings.dify_dataset_api_key}"},
                    params={"page": 1, "limit": 100},
                )
                response.raise_for_status()
                return response.json()
