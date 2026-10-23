from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sys
from pathlib import Path
from threading import Thread
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "adapters" / "model-api" / "src"),
]

from verdflow.contracts import (  # noqa: E402
    InferenceProtocol,
    MaterialClassification,
)
from verdflow.model_api import (  # noqa: E402
    EnvironmentSecretResolver,
    JsonHttpResponse,
    ModelApiClient,
    ModelApiProviderRegistry,
    ModelApiReasoningMode,
    ModelApiResponseError,
    ModelApiSecretError,
    ModelApiTransportError,
    UrllibJsonTransport,
    parse_provider_config_document,
    provider_preset,
    provider_presets,
)


FACT_REPORT = {
    "schema": "fbx-region-tagger.fact-report.v1",
    "materials": [
        {
            "name": "Grass_Main",
            "face_count": 12,
            "object_count": 1,
            "objects": ["Ground"],
            "diffuse_color": [0.1, 0.7, 0.2, 1.0],
            "textures": [{"image": "grass_albedo.png"}],
        },
        {
            "name": "Street_Grass",
            "face_count": 8,
            "object_count": 1,
            "objects": ["RoadGreenbelt"],
            "diffuse_color": [0.2, 0.6, 0.2, 1.0],
            "textures": [],
        },
    ],
}

MODEL_OUTPUT = {
    "materials": [
        {
            "material_name": "Grass_Main",
            "classification": "general_green",
            "confidence": 0.96,
            "evidence": ["material_name_contains_grass"],
        },
        {
            "material_name": "Street_Grass",
            "classification": "street_tree_green",
            "confidence": 0.99,
            "evidence": ["material_and_object_names_indicate_street_greenbelt"],
        },
    ],
    "warnings": [],
}


class RecordingTransport:
    def __init__(self, bodies: dict[str, dict[str, object]]) -> None:
        self.bodies = bodies
        self.calls: list[dict[str, object]] = []

    def post_json(
        self,
        url: str,
        *,
        headers,
        document,
        timeout_seconds: float,
    ) -> JsonHttpResponse:
        self.calls.append(
            {
                "url": url,
                "headers": dict(headers),
                "document": dict(document),
                "timeout_seconds": timeout_seconds,
            }
        )
        return JsonHttpResponse(200, {}, self.bodies[url])

    def get_json(
        self,
        url: str,
        *,
        headers,
        timeout_seconds: float,
    ) -> JsonHttpResponse:
        self.calls.append(
            {
                "url": url,
                "headers": dict(headers),
                "timeout_seconds": timeout_seconds,
            }
        )
        return JsonHttpResponse(200, {}, self.bodies[url])


class ModelApiAdapterTests(unittest.TestCase):
    def test_exposes_three_versioned_provider_presets(self) -> None:
        presets = provider_presets()
        self.assertEqual(
            [item.descriptor.provider_id for item in presets],
            ["openai", "anthropic", "deepseek"],
        )
        self.assertTrue(all(item.descriptor.default_model is None for item in presets))
        self.assertTrue(
            all(item.model_discovery_endpoint for item in presets)
        )

    def test_discovers_models_with_protocol_authentication(self) -> None:
        preset = provider_preset("anthropic")
        assert preset.model_discovery_endpoint is not None
        transport = RecordingTransport(
            {
                preset.model_discovery_endpoint: {
                    "data": [
                        {
                            "id": "claude-example",
                            "display_name": "Claude Example",
                        }
                    ]
                }
            }
        )
        models = ModelApiClient(transport).list_models(
            provider_id="anthropic",
            api_key="test-secret",
        )
        self.assertEqual(models[0].model_id, "claude-example")
        self.assertEqual(models[0].display_name, "Claude Example")
        self.assertEqual(transport.calls[0]["headers"]["x-api-key"], "test-secret")

    def test_maps_all_provider_protocols_to_one_candidate_contract(self) -> None:
        bodies = {
            provider_preset("openai").endpoint: {
                "id": "resp_openai",
                "output_text": json.dumps(MODEL_OUTPUT),
                "usage": {"input_tokens": 100, "output_tokens": 40},
            },
            provider_preset("anthropic").endpoint: {
                "id": "msg_anthropic",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "submit_material_classification",
                        "input": MODEL_OUTPUT,
                    }
                ],
                "usage": {"input_tokens": 101, "output_tokens": 41},
            },
            provider_preset("deepseek").endpoint: {
                "id": "chat_deepseek",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(MODEL_OUTPUT),
                            "reasoning_content": "must never be persisted",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 102,
                    "completion_tokens": 42,
                    "total_tokens": 144,
                },
            },
        }
        transport = RecordingTransport(bodies)
        client = ModelApiClient(transport)

        candidates = [
            client.analyze_materials(
                FACT_REPORT,
                provider_id=provider_id,
                api_key="test-secret",
                input_ref="artifact_facts",
                task_id=f"task_{provider_id}",
                model="test-model",
            )
            for provider_id in ("openai", "anthropic", "deepseek")
        ]

        for candidate in candidates:
            self.assertEqual(len(candidate.materials), 2)
            self.assertEqual(
                candidate.materials[0].classification,
                MaterialClassification.GENERAL_GREEN,
            )
            self.assertNotIn(
                "must never be persisted",
                json.dumps(candidate.to_document()),
            )

        openai_call, anthropic_call, deepseek_call = transport.calls
        self.assertEqual(
            openai_call["document"]["text"]["format"]["type"],
            "json_schema",
        )
        self.assertFalse(openai_call["document"]["store"])
        self.assertEqual(
            anthropic_call["document"]["tool_choice"]["name"],
            "submit_material_classification",
        )
        self.assertEqual(
            deepseek_call["document"]["response_format"],
            {"type": "json_object"},
        )
        self.assertEqual(
            deepseek_call["document"]["thinking"],
            {"type": "disabled"},
        )
        self.assertEqual(deepseek_call["document"]["max_tokens"], 32768)
        self.assertEqual(
            candidates[0].invocation.protocol,
            InferenceProtocol.OPENAI_RESPONSES,
        )

    def test_prompt_keeps_texture_filename_but_redacts_local_directory(self) -> None:
        report = json.loads(json.dumps(FACT_REPORT))
        report["materials"][0]["textures"][0]["filepath"] = (
            "D:\\Private\\Project\\grass_albedo.png"
        )
        endpoint = provider_preset("deepseek").endpoint
        transport = RecordingTransport(
            {
                endpoint: {
                    "id": "chat_redaction",
                    "choices": [
                        {
                            "message": {"content": json.dumps(MODEL_OUTPUT)},
                            "finish_reason": "stop",
                        }
                    ],
                }
            }
        )
        ModelApiClient(transport).analyze_materials(
            report,
            provider_id="deepseek",
            api_key="test-secret",
            input_ref="artifact_facts",
            model="test-model",
        )
        prompt = transport.calls[0]["document"]["messages"][1]["content"]
        self.assertIn("grass_albedo.png", prompt)
        self.assertNotIn("Private", prompt)
        self.assertNotIn("Project", prompt)

    def test_prompt_preserves_safe_source_relative_texture_path(self) -> None:
        report = json.loads(json.dumps(FACT_REPORT))
        report["materials"][0]["textures"][0] = {
            "image": "grass_albedo.png",
            "source_relative_path": "textures/ground/grass/grass_albedo.png",
            "path_scope": "source_relative",
        }
        endpoint = provider_preset("deepseek").endpoint
        transport = RecordingTransport(
            {
                endpoint: {
                    "id": "chat_relative_path",
                    "choices": [
                        {
                            "message": {"content": json.dumps(MODEL_OUTPUT)},
                            "finish_reason": "stop",
                        }
                    ],
                }
            }
        )
        ModelApiClient(transport).analyze_materials(
            report,
            provider_id="deepseek",
            api_key="test-secret",
            input_ref="artifact_facts",
            model="test-model",
        )
        prompt = transport.calls[0]["document"]["messages"][1]["content"]
        self.assertIn("textures/ground/grass/grass_albedo.png", prompt)

        report["materials"][0]["textures"][0]["source_relative_path"] = (
            "../private/grass_albedo.png"
        )
        with self.assertRaisesRegex(ValueError, "safe relative path"):
            ModelApiClient(transport).analyze_materials(
                report,
                provider_id="deepseek",
                api_key="test-secret",
                input_ref="artifact_facts",
                model="test-model",
            )

    def test_custom_provider_is_selectable_and_versioned_by_configuration(self) -> None:
        document = {
            "schema": "verdflow.model-api-provider-config",
            "version": "1",
            "providers": [
                {
                    "provider_id": "team-gateway",
                    "display_name": "Team Gateway",
                    "protocol": "openai-chat-completions",
                    "endpoint": "https://models.example.com/v1/chat/completions",
                    "default_model": "team-material-model",
                    "secret_ref": "model-api/custom/team-gateway",
                    "development_environment_variable": "TEAM_GATEWAY_API_KEY",
                }
            ],
        }
        first = parse_provider_config_document(document)
        second = parse_provider_config_document(document)
        self.assertEqual(
            first[0].descriptor.provider_version,
            second[0].descriptor.provider_version,
        )
        self.assertTrue(first[0].descriptor.provider_version.startswith("custom-"))
        self.assertFalse(first[0].builtin)

        response = {
            "id": "chat_custom",
            "choices": [
                {
                    "message": {"content": json.dumps(MODEL_OUTPUT)},
                    "finish_reason": "stop",
                }
            ],
        }
        transport = RecordingTransport({first[0].endpoint: response})
        registry = ModelApiProviderRegistry(first)
        candidate = ModelApiClient(
            transport,
            provider_registry=registry,
        ).analyze_materials(
            FACT_REPORT,
            provider_id="team-gateway",
            api_key="test-secret",
            input_ref="artifact_facts",
        )

        self.assertEqual(candidate.invocation.provider_id, "team-gateway")
        self.assertEqual(candidate.invocation.model, "team-material-model")
        self.assertEqual(transport.calls[0]["url"], first[0].endpoint)

    def test_call_plan_uses_provider_capabilities_without_cost_batching(self) -> None:
        plan = ModelApiClient().plan_material_analysis(
            FACT_REPORT,
            provider_id="deepseek",
            model="test-model",
        )
        self.assertEqual(plan.provider_context_window_tokens, 1_000_000)
        self.assertEqual(plan.provider_max_output_tokens, 384_000)
        self.assertEqual(plan.requested_max_output_tokens, 32_768)
        self.assertEqual(plan.material_count, 2)
        self.assertEqual(plan.warnings, ())

        warned = ModelApiClient().plan_material_analysis(
            FACT_REPORT,
            provider_id="deepseek",
            model="test-model",
            max_output_tokens=512,
        )
        self.assertIn("above 80%", warned.warnings[0])

    def test_chat_length_finish_reason_is_a_visible_failure(self) -> None:
        endpoint = provider_preset("deepseek").endpoint
        client = ModelApiClient(
            RecordingTransport(
                {
                    endpoint: {
                        "id": "truncated",
                        "choices": [
                            {
                                "message": {"content": "{\"materials\":["},
                                "finish_reason": "length",
                            }
                        ],
                    }
                }
            )
        )
        with self.assertRaisesRegex(ModelApiResponseError, "length"):
            client.analyze_materials(
                FACT_REPORT,
                provider_id="deepseek",
                api_key="test-secret",
                input_ref="artifact_facts",
                model="test-model",
            )

    def test_custom_provider_cannot_shadow_builtins_or_expand_execution_surface(self) -> None:
        base = {
            "schema": "verdflow.model-api-provider-config",
            "version": "1",
            "providers": [
                {
                    "provider_id": "custom",
                    "display_name": "Custom",
                    "protocol": "openai-chat-completions",
                    "endpoint": "https://models.example.com/v1/chat/completions",
                    "default_model": "custom-model",
                    "secret_ref": "model-api/custom/custom",
                }
            ],
        }
        shadow = json.loads(json.dumps(base))
        shadow["providers"][0]["provider_id"] = "deepseek"
        with self.assertRaisesRegex(ValueError, "Duplicate model API provider"):
            parse_provider_config_document(shadow)

        insecure = json.loads(json.dumps(base))
        insecure["providers"][0]["endpoint"] = "http://models.example.com/v1/chat"
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            parse_provider_config_document(insecure)

        unsupported = json.loads(json.dumps(base))
        unsupported["providers"][0]["protocol"] = "codex-sdk"
        with self.assertRaisesRegex(ValueError, "Unsupported model API protocol"):
            parse_provider_config_document(unsupported)

        injected = json.loads(json.dumps(base))
        injected["providers"][0]["headers"] = {"X-Custom": "value"}
        with self.assertRaisesRegex(ValueError, "unsupported fields"):
            parse_provider_config_document(injected)

        credentials = json.loads(json.dumps(base))
        credentials["providers"][0]["endpoint"] = (
            "https://user:password@models.example.com/v1/chat"
        )
        with self.assertRaisesRegex(ValueError, "credentials"):
            parse_provider_config_document(credentials)

        duplicate_secret = json.loads(json.dumps(base))
        duplicate_secret["providers"][0]["secret_ref"] = "model-api/deepseek"
        with self.assertRaisesRegex(ValueError, "Duplicate model API secret reference"):
            parse_provider_config_document(duplicate_secret)

    def test_custom_provider_capabilities_are_typed_and_versioned(self) -> None:
        document = {
            "schema": "verdflow.model-api-provider-config",
            "version": "1",
            "providers": [
                {
                    "provider_id": "deepseek-gateway",
                    "display_name": "DeepSeek Gateway",
                    "protocol": "openai-chat-completions",
                    "endpoint": "https://models.example.com/v1/chat/completions",
                    "default_model": "deepseek-v4-flash",
                    "capabilities": {
                        "context_window_tokens": 1000000,
                        "max_output_tokens": 384000,
                        "default_max_output_tokens": 32768,
                        "reasoning_mode": "disabled",
                    },
                }
            ],
        }
        first = parse_provider_config_document(document)[0]
        self.assertEqual(first.capabilities.context_window_tokens, 1_000_000)
        self.assertEqual(
            first.capabilities.reasoning_mode,
            ModelApiReasoningMode.DISABLED,
        )

        changed = json.loads(json.dumps(document))
        changed["providers"][0]["capabilities"]["default_max_output_tokens"] = 65536
        second = parse_provider_config_document(changed)[0]
        self.assertNotEqual(
            first.descriptor.provider_version,
            second.descriptor.provider_version,
        )

    def test_custom_provider_development_secret_mapping_is_explicit(self) -> None:
        providers = parse_provider_config_document(
            {
                "schema": "verdflow.model-api-provider-config",
                "version": "1",
                "providers": [
                    {
                        "provider_id": "team-gateway",
                        "display_name": "Team Gateway",
                        "protocol": "openai-chat-completions",
                        "endpoint": "https://models.example.com/v1/chat/completions",
                        "default_model": "team-model",
                        "development_environment_variable": "TEAM_GATEWAY_API_KEY",
                    }
                ],
            }
        )
        registry = ModelApiProviderRegistry(providers)
        resolver = EnvironmentSecretResolver(
            {"TEAM_GATEWAY_API_KEY": "custom-secret"},
            provider_registry=registry,
        )
        self.assertEqual(resolver.resolve_provider("team-gateway"), "custom-secret")

        with self.assertRaises(ModelApiSecretError) as raised:
            EnvironmentSecretResolver(
                {},
                provider_registry=registry,
            ).resolve_provider("team-gateway")
        self.assertNotIn("TEAM_GATEWAY_API_KEY", str(raised.exception))

    def test_rejects_missing_unknown_duplicate_and_invalid_materials(self) -> None:
        invalid_documents = (
            {
                "materials": MODEL_OUTPUT["materials"][:1],
                "warnings": [],
            },
            {
                "materials": [
                    *MODEL_OUTPUT["materials"],
                    {
                        "material_name": "Injected",
                        "classification": "not_green",
                        "confidence": 1,
                        "evidence": [],
                    },
                ],
                "warnings": [],
            },
            {
                "materials": [
                    MODEL_OUTPUT["materials"][0],
                    MODEL_OUTPUT["materials"][0],
                ],
                "warnings": [],
            },
            {
                "materials": [
                    {
                        **MODEL_OUTPUT["materials"][0],
                        "confidence": 1.1,
                    },
                    MODEL_OUTPUT["materials"][1],
                ],
                "warnings": [],
            },
        )
        endpoint = provider_preset("deepseek").endpoint
        for index, output in enumerate(invalid_documents):
            with self.subTest(index=index):
                client = ModelApiClient(
                    RecordingTransport(
                        {
                            endpoint: {
                                "id": "bad",
                                "choices": [
                                    {
                                        "message": {"content": json.dumps(output)},
                                        "finish_reason": "stop",
                                    }
                                ],
                            }
                        }
                    )
                )
                with self.assertRaises(ModelApiResponseError):
                    client.analyze_materials(
                        FACT_REPORT,
                        provider_id="deepseek",
                        api_key="test-secret",
                        input_ref="artifact_facts",
                        model="test-model",
                    )

    def test_development_secret_resolver_never_exposes_values_in_errors(self) -> None:
        resolver = EnvironmentSecretResolver({"DEEPSEEK_API_KEY": "secret-value"})
        self.assertEqual(resolver.resolve_provider("deepseek"), "secret-value")
        with self.assertRaises(ModelApiSecretError) as raised:
            EnvironmentSecretResolver({}).resolve_provider("deepseek")
        self.assertNotIn("DEEPSEEK_API_KEY", str(raised.exception))

    def test_stdlib_transport_allows_loopback_and_redacts_http_failure(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers["Content-Length"])
                self.rfile.read(length)
                self.send_response(401)
                self.send_header("x-request-id", "req_redacted_test")
                self.end_headers()

            def log_message(self, _format: str, *args: object) -> None:
                del args

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/v1/test"
            with self.assertRaises(ModelApiTransportError) as raised:
                UrllibJsonTransport().post_json(
                    url,
                    headers={"Authorization": "Bearer never-log-this"},
                    document={"input": "data"},
                    timeout_seconds=2,
                )
            message = str(raised.exception)
            self.assertIn("HTTP 401", message)
            self.assertIn("req_redacted_test", message)
            self.assertNotIn("never-log-this", message)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
