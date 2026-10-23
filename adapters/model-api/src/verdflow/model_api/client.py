"""Structured task client independent of supplier SDKs."""

from __future__ import annotations

from typing import Any, Mapping
import uuid

from verdflow.contracts import (
    MATERIAL_CLASSIFICATION_TASK,
    InferenceProtocol,
    MaterialClassificationCandidate,
    ModelInvocationInfo,
)

from . import anthropic_messages, openai_chat, openai_responses
from .material_classification import (
    MATERIAL_OUTPUT_SCHEMA,
    PROMPT_POLICY_VERSION,
    build_prompts,
    validate_output,
)
from .models import ModelApiModelInfo, ModelApiProviderConfig
from .models import MaterialAnalysisCallPlan, ModelApiReasoningMode
from .provider_config import ModelApiProviderRegistry
from .transport import JsonTransport, UrllibJsonTransport


class ModelApiClient:
    def __init__(
        self,
        transport: JsonTransport | None = None,
        *,
        provider_registry: ModelApiProviderRegistry | None = None,
    ) -> None:
        self._transport = transport or UrllibJsonTransport()
        self._provider_registry = provider_registry or ModelApiProviderRegistry()

    def providers(self) -> tuple[ModelApiProviderConfig, ...]:
        return self._provider_registry.list()

    def list_models(
        self,
        *,
        provider_id: str,
        api_key: str,
        timeout_seconds: float = 30.0,
    ) -> tuple[ModelApiModelInfo, ...]:
        provider = self._provider_registry.get(provider_id)
        endpoint = provider.model_discovery_endpoint
        if endpoint is None:
            raise ValueError(
                f"Model discovery is not configured for Provider: {provider_id}"
            )
        key = _required_text(api_key, "api_key")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        response = self._transport.get_json(
            endpoint,
            headers=_protocol_headers(provider.descriptor.protocol, key),
            timeout_seconds=timeout_seconds,
        )
        rows = response.body.get("data")
        if not isinstance(rows, list):
            raise ValueError("Model discovery response data must be an array")
        models: list[ModelApiModelInfo] = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            model_id = row.get("id")
            if not isinstance(model_id, str) or not model_id.strip():
                continue
            normalized = model_id.strip()
            if normalized in seen:
                continue
            seen.add(normalized)
            display_name = row.get("display_name")
            owned_by = row.get("owned_by")
            models.append(
                ModelApiModelInfo(
                    model_id=normalized,
                    display_name=(
                        display_name.strip()
                        if isinstance(display_name, str) and display_name.strip()
                        else None
                    ),
                    owned_by=(
                        owned_by.strip()
                        if isinstance(owned_by, str) and owned_by.strip()
                        else None
                    ),
                )
            )
        if not models:
            raise ValueError("Model discovery returned no selectable models")
        return tuple(models)

    def plan_material_analysis(
        self,
        fact_report: Mapping[str, Any],
        *,
        provider_id: str,
        model: str | None = None,
        max_output_tokens: int | None = None,
    ) -> MaterialAnalysisCallPlan:
        provider = self._provider_registry.get(provider_id)
        selected_model = _required_text(
            model or provider.descriptor.default_model,
            "model",
        )
        system_prompt, user_prompt, names = build_prompts(fact_report)
        estimated_input = _estimate_tokens(system_prompt + user_prompt)
        estimated_output = 512 + len(names) * 96
        capabilities = provider.capabilities
        requested = (
            _validated_token_limit(max_output_tokens)
            if max_output_tokens is not None
            else max(
                capabilities.default_max_output_tokens,
                _round_up(estimated_output * 5 // 4, 1024),
            )
        )
        physical_output_limit = capabilities.max_output_tokens
        if capabilities.context_window_tokens is not None:
            context_remainder = capabilities.context_window_tokens - estimated_input
            if context_remainder < 256:
                raise ValueError(
                    "Material analysis input exceeds Provider context window"
                )
            physical_output_limit = (
                context_remainder
                if physical_output_limit is None
                else min(physical_output_limit, context_remainder)
            )
        if physical_output_limit is not None and requested > physical_output_limit:
            if max_output_tokens is not None:
                raise ValueError(
                    "max_output_tokens exceeds Provider physical limit"
                )
            requested = physical_output_limit
        if physical_output_limit is not None and estimated_output > physical_output_limit:
            raise ValueError(
                "Estimated material output exceeds Provider physical limit; batching is required"
            )
        warnings: list[str] = []
        if estimated_output > requested * 0.8:
            warnings.append(
                "Estimated output is above 80% of the requested output limit"
            )
        if (
            capabilities.context_window_tokens is not None
            and estimated_input + estimated_output
            > capabilities.context_window_tokens * 0.8
        ):
            warnings.append(
                "Estimated input and output are above 80% of the context window"
            )
        return MaterialAnalysisCallPlan(
            provider_id=provider.descriptor.provider_id,
            model=selected_model,
            material_count=len(names),
            estimated_input_tokens=estimated_input,
            estimated_output_tokens=estimated_output,
            requested_max_output_tokens=requested,
            provider_context_window_tokens=capabilities.context_window_tokens,
            provider_max_output_tokens=capabilities.max_output_tokens,
            warnings=tuple(warnings),
        )

    def analyze_materials(
        self,
        fact_report: Mapping[str, Any],
        *,
        provider_id: str,
        api_key: str,
        input_ref: str,
        model: str | None = None,
        task_id: str | None = None,
        timeout_seconds: float = 120.0,
        max_output_tokens: int | None = None,
    ) -> MaterialClassificationCandidate:
        provider = self._provider_registry.get(provider_id)
        key = _required_text(api_key, "api_key")
        source_ref = _required_text(input_ref, "input_ref")
        selected_model = _required_text(
            model or provider.descriptor.default_model,
            "model",
        )
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        plan = self.plan_material_analysis(
            fact_report,
            provider_id=provider_id,
            model=selected_model,
            max_output_tokens=max_output_tokens,
        )
        system_prompt, user_prompt, expected_names = build_prompts(fact_report)
        request, headers = _protocol_request(
            provider.descriptor.protocol,
            model=selected_model,
            api_key=key,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_output_tokens=plan.requested_max_output_tokens,
            reasoning_mode=provider.capabilities.reasoning_mode,
        )
        response = self._transport.post_json(
            provider.endpoint,
            headers=headers,
            document=request,
            timeout_seconds=timeout_seconds,
        )
        protocol_response = _decode_protocol_response(
            provider.descriptor.protocol,
            response.body,
        )
        materials, warnings = validate_output(
            protocol_response.output,
            expected_names,
        )
        return MaterialClassificationCandidate(
            task_id=task_id or f"analysis_{uuid.uuid4().hex}",
            task_type=MATERIAL_CLASSIFICATION_TASK,
            input_ref=source_ref,
            invocation=ModelInvocationInfo(
                provider_id=provider.descriptor.provider_id,
                provider_version=provider.descriptor.provider_version,
                protocol=provider.descriptor.protocol,
                model=selected_model,
                prompt_policy_version=PROMPT_POLICY_VERSION,
                request_id=protocol_response.request_id,
                usage=protocol_response.usage,
            ),
            materials=materials,
            warnings=warnings,
        )


def _protocol_request(
    protocol: InferenceProtocol,
    *,
    model: str,
    api_key: str,
    system_prompt: str,
    user_prompt: str,
    max_output_tokens: int,
    reasoning_mode: ModelApiReasoningMode,
) -> tuple[dict[str, Any], dict[str, str]]:
    common = {
        "model": model,
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "max_output_tokens": max_output_tokens,
    }
    if protocol is InferenceProtocol.OPENAI_RESPONSES:
        return (
            openai_responses.build_request(
                **common,
                output_schema=MATERIAL_OUTPUT_SCHEMA,
            ),
            openai_responses.headers(api_key),
        )
    if protocol is InferenceProtocol.ANTHROPIC_MESSAGES:
        return (
            anthropic_messages.build_request(
                **common,
                output_schema=MATERIAL_OUTPUT_SCHEMA,
            ),
            anthropic_messages.headers(api_key),
        )
    if protocol is InferenceProtocol.OPENAI_CHAT_COMPLETIONS:
        return (
            openai_chat.build_request(
                **common,
                reasoning_mode=reasoning_mode,
            ),
            openai_chat.headers(api_key),
        )
    raise ValueError(f"Unsupported model API protocol: {protocol.value}")


def _decode_protocol_response(
    protocol: InferenceProtocol,
    body: Mapping[str, Any],
):
    if protocol is InferenceProtocol.OPENAI_RESPONSES:
        return openai_responses.decode_response(body)
    if protocol is InferenceProtocol.ANTHROPIC_MESSAGES:
        return anthropic_messages.decode_response(body)
    if protocol is InferenceProtocol.OPENAI_CHAT_COMPLETIONS:
        return openai_chat.decode_response(body)
    raise ValueError(f"Unsupported model API protocol: {protocol.value}")


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value.strip()


def _protocol_headers(protocol: InferenceProtocol, api_key: str) -> dict[str, str]:
    if protocol is InferenceProtocol.OPENAI_RESPONSES:
        return openai_responses.headers(api_key)
    if protocol is InferenceProtocol.ANTHROPIC_MESSAGES:
        return anthropic_messages.headers(api_key)
    if protocol is InferenceProtocol.OPENAI_CHAT_COMPLETIONS:
        return openai_chat.headers(api_key)
    raise ValueError(f"Unsupported Model API protocol: {protocol.value}")


def _validated_token_limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 256:
        raise ValueError("max_output_tokens must be at least 256")
    return value


def _estimate_tokens(value: str) -> int:
    return max(1, (len(value) + 3) // 4)


def _round_up(value: int, step: int) -> int:
    return ((value + step - 1) // step) * step
