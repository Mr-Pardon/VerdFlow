"""System-scoped custom model Provider configuration and lookup."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from typing import Any, Iterable, Mapping

from verdflow.contracts import (
    MATERIAL_CLASSIFICATION_TASK,
    InferenceProtocol,
    InferenceProviderDescriptor,
    InferenceProviderKind,
)

from .models import (
    ModelApiCapabilities,
    ModelApiProviderConfig,
    ModelApiReasoningMode,
)
from .presets import provider_presets
from .transport import validate_endpoint


MODEL_API_PROVIDER_CONFIG_SCHEMA = "verdflow.model-api-provider-config"
MODEL_API_PROVIDER_CONFIG_VERSION = "1"
MAX_CUSTOM_PROVIDERS = 100
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_ENVIRONMENT_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
_SECRET_REFERENCE = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,127}$")
_SUPPORTED_PROTOCOLS = {
    InferenceProtocol.OPENAI_RESPONSES,
    InferenceProtocol.ANTHROPIC_MESSAGES,
    InferenceProtocol.OPENAI_CHAT_COMPLETIONS,
}


class ModelApiProviderRegistry:
    """Immutable lookup assembled from built-in templates and user configs."""

    def __init__(
        self,
        custom_providers: Iterable[ModelApiProviderConfig] = (),
        *,
        include_builtin: bool = True,
        selected_models: Mapping[str, str] | None = None,
    ) -> None:
        providers = [*provider_presets()] if include_builtin else []
        providers.extend(custom_providers)
        selections = selected_models or {}
        self._providers: dict[str, ModelApiProviderConfig] = {}
        secret_references: set[str] = set()
        for provider in providers:
            selected_model = selections.get(provider.descriptor.provider_id)
            if selected_model is not None:
                provider = replace(
                    provider,
                    descriptor=replace(
                        provider.descriptor,
                        default_model=_required_text(selected_model, "model"),
                    ),
                )
            validate_provider_config(provider)
            provider_id = provider.descriptor.provider_id
            if provider_id in self._providers:
                raise ValueError(f"Duplicate model API provider: {provider_id}")
            if provider.secret_ref in secret_references:
                raise ValueError(
                    f"Duplicate model API secret reference: {provider.secret_ref}"
                )
            self._providers[provider_id] = provider
            secret_references.add(provider.secret_ref)
        if not self._providers:
            raise ValueError("At least one model API provider is required")

    def get(self, provider_id: str) -> ModelApiProviderConfig:
        normalized = _required_text(provider_id, "provider_id")
        try:
            return self._providers[normalized]
        except KeyError as error:
            raise ValueError(f"Unknown model API provider: {provider_id}") from error

    def list(self) -> tuple[ModelApiProviderConfig, ...]:
        return tuple(self._providers.values())


def parse_provider_config_document(
    document: Mapping[str, Any],
) -> tuple[ModelApiProviderConfig, ...]:
    if not isinstance(document, Mapping):
        raise ValueError("Model API provider config must be an object")
    if document.get("schema") != MODEL_API_PROVIDER_CONFIG_SCHEMA:
        raise ValueError("Model API provider config schema is unsupported")
    if str(document.get("version")) != MODEL_API_PROVIDER_CONFIG_VERSION:
        raise ValueError("Model API provider config version is unsupported")
    rows = document.get("providers")
    if not isinstance(rows, list):
        raise ValueError("Model API provider config providers must be an array")
    if len(rows) > MAX_CUSTOM_PROVIDERS:
        raise ValueError(
            f"Model API provider config exceeds {MAX_CUSTOM_PROVIDERS} providers"
        )
    providers = tuple(_parse_provider(row) for row in rows)
    ModelApiProviderRegistry(providers)
    return providers


def provider_config_to_document(
    provider: ModelApiProviderConfig,
) -> dict[str, Any]:
    """Return the canonical, secret-free row accepted by the config parser."""
    validate_provider_config(provider)
    capabilities = provider.capabilities
    return {
        "provider_id": provider.descriptor.provider_id,
        "display_name": provider.descriptor.display_name,
        "protocol": provider.descriptor.protocol.value,
        "endpoint": provider.endpoint,
        "model_discovery_endpoint": provider.model_discovery_endpoint,
        "default_model": provider.descriptor.default_model,
        "secret_ref": provider.secret_ref,
        "development_environment_variable": (
            provider.development_environment_variable
        ),
        "capabilities": {
            "context_window_tokens": capabilities.context_window_tokens,
            "max_output_tokens": capabilities.max_output_tokens,
            "default_max_output_tokens": (
                capabilities.default_max_output_tokens
            ),
            "reasoning_mode": capabilities.reasoning_mode.value,
        },
    }


def provider_config_collection_document(
    providers: Iterable[ModelApiProviderConfig],
) -> dict[str, Any]:
    rows = [provider_config_to_document(provider) for provider in providers]
    return {
        "schema": MODEL_API_PROVIDER_CONFIG_SCHEMA,
        "version": MODEL_API_PROVIDER_CONFIG_VERSION,
        "providers": sorted(rows, key=lambda row: str(row["provider_id"])),
    }


def validate_provider_config(config: ModelApiProviderConfig) -> None:
    descriptor = config.descriptor
    if descriptor.kind is not InferenceProviderKind.API:
        raise ValueError("Model API provider kind must be api")
    if not _IDENTIFIER.fullmatch(descriptor.provider_id):
        raise ValueError("provider_id must use lowercase identifier characters")
    _required_text(descriptor.display_name, "display_name")
    _required_text(descriptor.provider_version, "provider_version")
    if descriptor.default_model is not None:
        _required_text(descriptor.default_model, "default_model")
    elif not config.builtin:
        raise ValueError("default_model is required")
    if descriptor.protocol not in _SUPPORTED_PROTOCOLS:
        raise ValueError(
            f"Unsupported model API protocol: {descriptor.protocol.value}"
        )
    if descriptor.task_types != (MATERIAL_CLASSIFICATION_TASK,):
        raise ValueError(
            "Model API provider currently supports only material_classification.v1"
        )
    validate_endpoint(config.endpoint)
    if config.model_discovery_endpoint is not None:
        validate_endpoint(config.model_discovery_endpoint)
    if not _SECRET_REFERENCE.fullmatch(config.secret_ref):
        raise ValueError("secret_ref must use lowercase path identifier characters")
    environment_name = config.development_environment_variable
    if environment_name is not None and not _ENVIRONMENT_NAME.fullmatch(
        environment_name
    ):
        raise ValueError("development_environment_variable is invalid")
    capabilities = config.capabilities
    for field in (
        "context_window_tokens",
        "max_output_tokens",
        "default_max_output_tokens",
    ):
        value = getattr(capabilities, field)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 256
        ):
            raise ValueError(f"capabilities.{field} must be at least 256")
    if (
        capabilities.max_output_tokens is not None
        and capabilities.default_max_output_tokens
        > capabilities.max_output_tokens
    ):
        raise ValueError(
            "capabilities.default_max_output_tokens exceeds max_output_tokens"
        )
    if (
        capabilities.context_window_tokens is not None
        and capabilities.max_output_tokens is not None
        and capabilities.max_output_tokens > capabilities.context_window_tokens
    ):
        raise ValueError(
            "capabilities.max_output_tokens exceeds context_window_tokens"
        )
    if (
        capabilities.reasoning_mode is not ModelApiReasoningMode.PROVIDER_DEFAULT
        and descriptor.protocol is not InferenceProtocol.OPENAI_CHAT_COMPLETIONS
    ):
        raise ValueError(
            "Configured reasoning mode requires openai-chat-completions"
        )


def _parse_provider(value: object) -> ModelApiProviderConfig:
    if not isinstance(value, Mapping):
        raise ValueError("Custom model API provider must be an object")
    allowed = {
        "provider_id",
        "display_name",
        "protocol",
        "endpoint",
        "model_discovery_endpoint",
        "default_model",
        "secret_ref",
        "development_environment_variable",
        "capabilities",
    }
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(
            "Custom model API provider has unsupported fields: "
            + ", ".join(sorted(map(str, unknown)))
        )
    provider_id = _required_text(value.get("provider_id"), "provider_id")
    protocol_text = _required_text(value.get("protocol"), "protocol")
    try:
        protocol = InferenceProtocol(protocol_text)
    except ValueError as error:
        raise ValueError(f"Unsupported model API protocol: {protocol_text}") from error
    canonical = {
        "provider_id": provider_id,
        "display_name": _required_text(value.get("display_name"), "display_name"),
        "protocol": protocol.value,
        "endpoint": _required_text(value.get("endpoint"), "endpoint"),
        "model_discovery_endpoint": _optional_text(
            value.get("model_discovery_endpoint"),
            "model_discovery_endpoint",
        ),
        "default_model": _required_text(value.get("default_model"), "default_model"),
        "secret_ref": _required_text(
            value.get("secret_ref") or f"model-api/custom/{provider_id}",
            "secret_ref",
        ),
        "development_environment_variable": _optional_text(
            value.get("development_environment_variable"),
            "development_environment_variable",
        ),
        "capabilities": _parse_capabilities(value.get("capabilities")),
    }
    digest = hashlib.sha256(
        json.dumps(
            _canonical_provider_document(canonical),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    config = ModelApiProviderConfig(
        descriptor=InferenceProviderDescriptor(
            provider_id=provider_id,
            display_name=canonical["display_name"],
            kind=InferenceProviderKind.API,
            protocol=protocol,
            provider_version=f"custom-{digest[:16]}",
            default_model=canonical["default_model"],
            task_types=(MATERIAL_CLASSIFICATION_TASK,),
        ),
        endpoint=canonical["endpoint"],
        model_discovery_endpoint=canonical["model_discovery_endpoint"],
        secret_ref=canonical["secret_ref"],
        development_environment_variable=canonical[
            "development_environment_variable"
        ],
        capabilities=canonical["capabilities"],
        builtin=False,
    )
    validate_provider_config(config)
    return config


def _parse_capabilities(value: object) -> ModelApiCapabilities:
    if value is None:
        return ModelApiCapabilities()
    if not isinstance(value, Mapping):
        raise ValueError("capabilities must be an object")
    allowed = {
        "context_window_tokens",
        "max_output_tokens",
        "default_max_output_tokens",
        "reasoning_mode",
    }
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(
            "capabilities has unsupported fields: "
            + ", ".join(sorted(map(str, unknown)))
        )
    try:
        reasoning_mode = ModelApiReasoningMode(
            value.get("reasoning_mode", ModelApiReasoningMode.PROVIDER_DEFAULT.value)
        )
    except ValueError as error:
        raise ValueError("capabilities.reasoning_mode is invalid") from error
    return ModelApiCapabilities(
        context_window_tokens=_optional_positive_int(
            value.get("context_window_tokens"),
            "capabilities.context_window_tokens",
        ),
        max_output_tokens=_optional_positive_int(
            value.get("max_output_tokens"),
            "capabilities.max_output_tokens",
        ),
        default_max_output_tokens=_positive_int(
            value.get("default_max_output_tokens", 8192),
            "capabilities.default_max_output_tokens",
        ),
        reasoning_mode=reasoning_mode,
    )


def _canonical_provider_document(canonical: Mapping[str, Any]) -> dict[str, Any]:
    capabilities = canonical["capabilities"]
    assert isinstance(capabilities, ModelApiCapabilities)
    return {
        **canonical,
        "capabilities": {
            "context_window_tokens": capabilities.context_window_tokens,
            "max_output_tokens": capabilities.max_output_tokens,
            "default_max_output_tokens": capabilities.default_max_output_tokens,
            "reasoning_mode": capabilities.reasoning_mode.value,
        },
    }


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value.strip()


def _optional_text(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field)


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 256:
        raise ValueError(f"{field} must be at least 256")
    return value


def _optional_positive_int(value: object, field: str) -> int | None:
    if value is None:
        return None
    return _positive_int(value, field)
