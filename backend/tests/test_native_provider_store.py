import json
import tempfile
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, mock

from app.modules.platform_settings.infrastructure import provider_store, runtime_settings
from app.modules.platform_settings.infrastructure.provider_runtime import (
    ModelProviderConfigurationError,
    get_active_model_provider,
    get_local_provider_summaries,
)


class NativeProviderStoreTests(IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.runtime_path = Path(self.temporary_directory.name) / "runtime_settings.json"
        self.path_patch = mock.patch.object(
            runtime_settings, "RUNTIME_SETTINGS_PATH", self.runtime_path
        )
        self.path_patch.start()

    def tearDown(self) -> None:
        self.path_patch.stop()
        self.temporary_directory.cleanup()

    async def test_save_and_activate_are_fully_local(self) -> None:
        saved = await provider_store.save_provider_settings(
            "gapgpt", "secret-token", "qwen-2.5", None
        )
        self.assertEqual(saved["name"], "گپ جی پی تی (GapGPT)")
        self.assertEqual(saved["api_key_masked"], "secret...oken")
        self.assertFalse(saved["is_active"])

        providers = await provider_store.activate_provider("gapgpt")

        self.assertTrue(providers[0]["is_active"])
        runtime = get_active_model_provider()
        self.assertEqual(runtime.provider_id, "gapgpt")
        self.assertEqual(runtime.base_url, "https://api.gapgpt.app/v1")

    async def test_blank_secret_preserves_existing_secret(self) -> None:
        await provider_store.save_provider_settings(
            "deepseek", "original-secret", "deepseek-chat", None
        )
        await provider_store.save_provider_settings(
            "deepseek", "", "deepseek-reasoner", ""
        )

        payload = json.loads(self.runtime_path.read_text(encoding="utf-8"))
        record = payload["model_providers"][0]
        self.assertEqual(record["api_key"], "original-secret")
        self.assertEqual(record["active_model"], "deepseek-reasoner")

    async def test_active_provider_cannot_be_deleted(self) -> None:
        await provider_store.save_provider_settings(
            "avalai", "secret-token", "gpt-4o-mini", None
        )
        await provider_store.activate_provider("avalai")

        with self.assertRaises(ModelProviderConfigurationError):
            await provider_store.delete_provider_settings("avalai")

        self.assertEqual(len(get_local_provider_summaries()), 1)

    async def test_unsupported_native_provider_is_rejected(self) -> None:
        with self.assertRaises(ModelProviderConfigurationError):
            await provider_store.save_provider_settings(
                "anthropic", "secret-token", "claude", None
            )

    def test_public_provider_commands_use_project_store(self) -> None:
        commands_path = (
            Path(__file__).parents[1]
            / "app/modules/platform_settings/infrastructure/provider_commands.py"
        )
        source = commands_path.read_text(encoding="utf-8")
        self.assertIn("provider_store", source)
