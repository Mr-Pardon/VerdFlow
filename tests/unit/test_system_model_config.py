from __future__ import annotations

import json
from dataclasses import asdict
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "asset-package" / "src"),
    str(ROOT / "packages" / "worker-runtime" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
    str(ROOT / "packages" / "library-store" / "src"),
    str(ROOT / "packages" / "planting" / "src"),
    str(ROOT / "packages" / "profile-package" / "src"),
    str(ROOT / "packages" / "core" / "src"),
    str(ROOT / "adapters" / "blender-worker" / "src"),
    str(ROOT / "adapters" / "dts" / "src"),
    str(ROOT / "adapters" / "model-api" / "src"),
    str(ROOT / "adapters" / "strategy-worker" / "src"),
    str(ROOT / "apps" / "core-host" / "src"),
    str(ROOT / "apps" / "system-host" / "src"),
]

from verdflow.system_host import (  # noqa: E402
    InMemorySecretStore,
    ModelProviderConfigStore,
    SecretNotFoundError,
    SystemHost,
    SystemHostConfig,
    WindowsCredentialSecretStore,
)
from verdflow.model_api import ModelApiTransportError  # noqa: E402


def _custom_provider(*, model: str = "material-v1") -> dict[str, object]:
    return {
        "provider_id": "team-gateway",
        "display_name": "Team Gateway",
        "protocol": "openai-chat-completions",
        "endpoint": "https://models.example.com/v1/chat/completions",
        "default_model": model,
        "secret_ref": "model-api/custom/team-gateway",
        "capabilities": {
            "context_window_tokens": 65536,
            "max_output_tokens": 8192,
            "default_max_output_tokens": 4096,
            "reasoning_mode": "disabled",
        },
    }


class ModelProviderConfigStoreTests(unittest.TestCase):
    def test_builtin_model_selection_is_machine_scoped_and_persistent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = ModelProviderConfigStore(root)
            store.prepare()
            self.assertIsNone(
                store.registry().get("openai").descriptor.default_model
            )

            selected = store.set_selected_model("openai", "gpt-example")
            self.assertEqual(selected.descriptor.default_model, "gpt-example")

            reopened = ModelProviderConfigStore(root)
            reopened.prepare()
            self.assertEqual(
                reopened.registry().get("openai").descriptor.default_model,
                "gpt-example",
            )

    def test_custom_provider_round_trips_updates_and_deletes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = ModelProviderConfigStore(root)
            store.prepare()

            first = store.upsert(_custom_provider())
            reopened = ModelProviderConfigStore(root)
            reopened.prepare()
            loaded = reopened.registry().get("team-gateway")
            updated = reopened.upsert(_custom_provider(model="material-v2"))

            self.assertEqual(
                loaded.descriptor.provider_version,
                first.descriptor.provider_version,
            )
            self.assertNotEqual(
                updated.descriptor.provider_version,
                first.descriptor.provider_version,
            )
            self.assertEqual(len(reopened.registry().list()), 4)
            serialized = (root / "model-providers.json").read_text(encoding="utf-8")
            self.assertNotIn("api_key", serialized)
            self.assertNotIn("secret-value", serialized)

            removed = reopened.delete("team-gateway")
            self.assertEqual(removed.descriptor.provider_id, "team-gateway")
            self.assertEqual(len(reopened.registry().list()), 3)

    def test_rejects_builtin_shadow_and_malformed_persisted_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = ModelProviderConfigStore(root)
            store.prepare()
            shadow = _custom_provider()
            shadow["provider_id"] = "deepseek"

            with self.assertRaisesRegex(ValueError, "Duplicate model API provider"):
                store.upsert(shadow)

            (root / "model-providers.json").write_text("[]", encoding="utf-8")
            with self.assertRaises(ValueError):
                ModelProviderConfigStore(root).prepare()


class SystemSecretStoreTests(unittest.TestCase):
    def test_provider_connection_test_is_ephemeral_and_sanitized(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            secrets = InMemorySecretStore()
            with SystemHost(
                SystemHostConfig(
                    library_path=root / "library",
                    system_path=root / "system",
                ),
                secret_store=secrets,
            ) as system:
                system.set_model_provider_secret("deepseek", "test-secret")
                system.set_model_provider_model("deepseek", "deepseek-test")
                with patch(
                    "verdflow.system_host.host.ModelApiClient.analyze_materials"
                ) as analyze:
                    result = system.test_model_provider("deepseek")
                self.assertTrue(result.succeeded)
                self.assertEqual(result.provider_id, "deepseek")
                self.assertEqual(analyze.call_count, 1)
                self.assertEqual(
                    analyze.call_args.kwargs["input_ref"],
                    "system:provider-connection-test",
                )

                with patch(
                    "verdflow.system_host.host.ModelApiClient.analyze_materials",
                    side_effect=ModelApiTransportError(
                        "Model API returned HTTP 401"
                    ),
                ):
                    failed = system.test_model_provider("deepseek")
                self.assertFalse(failed.succeeded)
                self.assertEqual(
                    failed.message,
                    "Model API returned HTTP 401",
                )
                self.assertNotIn("test-secret", failed.message)

    def test_provider_snapshot_and_secret_resolver_reach_project_runtime(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            secrets = InMemorySecretStore()
            with SystemHost(
                SystemHostConfig(
                    library_path=root / "library",
                    system_path=root / "system",
                ),
                secret_store=secrets,
            ) as system:
                provider = system.upsert_model_provider(_custom_provider())
                system.set_model_provider_secret(
                    provider.provider_id,
                    "project-runtime-secret",
                )

                session = system.create_project(
                    root / "project",
                    project_name="Model API Project",
                    enable_blender_worker=False,
                    enable_model_api_worker=True,
                )

                self.assertEqual(session.state.value, "active")
                self.assertEqual(system.status().active_project_count, 1)

    def test_system_host_exposes_secret_status_without_value(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            secrets = InMemorySecretStore()
            with SystemHost(
                SystemHostConfig(
                    library_path=root / "library",
                    system_path=root / "system",
                ),
                secret_store=secrets,
            ) as system:
                custom = system.upsert_model_provider(_custom_provider())
                configured = system.set_model_provider_secret(
                    custom.provider_id,
                    "secret-value",
                )

                self.assertTrue(configured.secret_configured)
                document = json.dumps(
                    [asdict(item) for item in system.list_model_providers()],
                    default=str,
                )
                self.assertNotIn("secret-value", document)
                self.assertFalse(
                    system.delete_model_provider_secret(
                        custom.provider_id
                    ).secret_configured
                )

    @unittest.skipUnless(os.name == "nt", "Windows credential test")
    def test_windows_credential_manager_round_trip_is_cleaned_up(self) -> None:
        store = WindowsCredentialSecretStore()
        secret_ref = f"model-api/test/{uuid4().hex}"
        try:
            self.assertFalse(store.contains(secret_ref))
            store.set(secret_ref, "temporary-test-secret")
            self.assertTrue(store.contains(secret_ref))
            self.assertEqual(store.resolve(secret_ref), "temporary-test-secret")
            self.assertTrue(store.delete(secret_ref))
            with self.assertRaises(SecretNotFoundError):
                store.resolve(secret_ref)
        finally:
            store.delete(secret_ref)


if __name__ == "__main__":
    unittest.main()
