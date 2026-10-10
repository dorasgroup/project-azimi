import json
from unittest import IsolatedAsyncioTestCase, TestCase

import httpx

from app.modules.analysis.domain import ModelGenerationRequest
from app.modules.analysis.infrastructure.model_gateway import (
    ModelGatewayError,
    OpenAICompatibleModelGateway,
)
from app.modules.platform_settings.domain import ModelProviderRuntime
from app.modules.platform_settings.infrastructure.provider_runtime import (
    ModelProviderConfigurationError,
    resolve_provider_runtime,
)


class ProviderRuntimeTests(TestCase):
    def test_gapgpt_resolves_to_direct_endpoint(self) -> None:
        runtime = resolve_provider_runtime(
            {"id": "gapgpt", "active_model": "gapgpt-qwen", "api_key": "secret"}
        )
        self.assertEqual(runtime.base_url, "https://api.gapgpt.app/v1")
        self.assertEqual(runtime.model, "gapgpt-qwen")
        self.assertNotIn("secret", repr(runtime))

    def test_openrouter_uses_existing_worker_route(self) -> None:
        runtime = resolve_provider_runtime(
            {"id": "openrouter", "active_model": "vendor/model", "api_key": "key"},
            worker_url="https://worker.example",
            worker_secret="worker-secret",
        )
        self.assertEqual(runtime.base_url, "https://worker.example/openrouter/v1")
        self.assertTrue(runtime.api_key.startswith("teb1."))

    def test_unsupported_provider_fails_closed(self) -> None:
        with self.assertRaises(ModelProviderConfigurationError):
            resolve_provider_runtime(
                {"id": "anthropic", "active_model": "claude", "api_key": "secret"}
            )


class ModelGatewayTests(IsolatedAsyncioTestCase):
    def test_malformed_object_is_repaired_locally(self) -> None:
        malformed = '{"items":[{"name":"first"} {"name":"second"}]}'

        parsed = OpenAICompatibleModelGateway._parse_json_object(malformed)

        self.assertEqual(
            parsed,
            {"items": [{"name": "first"}, {"name": "second"}]},
        )

    async def test_generate_json_uses_provider_neutral_contract(self) -> None:
        captured: dict[str, object] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured["authorization"] = request.headers.get("Authorization")
            captured["payload"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "```json\n{\"ok\": true}\n```"}}]},
            )

        gateway = OpenAICompatibleModelGateway(
            ModelProviderRuntime(
                provider_id="test-provider",
                model="test-model",
                base_url="https://model.test/v1",
                api_key="test-secret",
            ),
            transport=httpx.MockTransport(handler),
        )
        result = await gateway.generate_json(
            ModelGenerationRequest(system_prompt="system", user_prompt="user")
        )

        self.assertEqual(result.content, {"ok": True})
        self.assertEqual(result.provider_id, "test-provider")
        self.assertEqual(captured["authorization"], "Bearer test-secret")
        self.assertEqual(captured["payload"]["response_format"], {"type": "json_object"})

    async def test_invalid_model_json_is_rejected(self) -> None:
        calls = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "not-json"}}]},
            )

        gateway = OpenAICompatibleModelGateway(
            ModelProviderRuntime("test", "model", "https://model.test/v1", "secret"),
            transport=httpx.MockTransport(handler),
        )
        with self.assertRaises(ModelGatewayError):
            await gateway.generate_json(ModelGenerationRequest("system", "user"))
        self.assertEqual(calls, 2)

    async def test_invalid_json_is_retried_with_stricter_instruction(self) -> None:
        payloads: list[dict[str, object]] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            payloads.append(json.loads(request.content))
            content = "not-json" if len(payloads) == 1 else '{"ok": true}'
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

        gateway = OpenAICompatibleModelGateway(
            ModelProviderRuntime("test", "model", "https://model.test/v1", "secret"),
            transport=httpx.MockTransport(handler),
        )

        result = await gateway.generate_json(ModelGenerationRequest("system", "user"))

        self.assertEqual(result.content, {"ok": True})
        self.assertEqual(len(payloads), 2)
        self.assertEqual(payloads[1]["temperature"], 0.0)
        self.assertIn("JSON syntax repair engine", payloads[1]["messages"][0]["content"])
        self.assertIn("not-json", payloads[1]["messages"][1]["content"])
        self.assertNotIn("system", payloads[1]["messages"][0]["content"])
