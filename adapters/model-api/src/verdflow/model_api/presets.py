"""Built-in provider presets; projects never own these credentials."""

from __future__ import annotations

from verdflow.contracts import (
    MATERIAL_CLASSIFICATION_TASK,
    InferenceProtocol,
    InferenceProviderDescriptor,
    InferenceProviderKind,
)

from .models import (
    ModelApiCapabilities,
    ModelApiPreset,
    ModelApiReasoningMode,
)


PRESET_VERSION = "2026-09-18"


_PRESETS = (
    ModelApiPreset(
        descriptor=InferenceProviderDescriptor(
            provider_id="openai",
            display_name="OpenAI",
            kind=InferenceProviderKind.API,
            protocol=InferenceProtocol.OPENAI_RESPONSES,
            provider_version=PRESET_VERSION,
            default_model=None,
            task_types=(MATERIAL_CLASSIFICATION_TASK,),
        ),
        endpoint="https://api.openai.com/v1/responses",
        model_discovery_endpoint="https://api.openai.com/v1/models",
        secret_ref="model-api/openai",
        development_environment_variable="OPENAI_API_KEY",
    ),
    ModelApiPreset(
        descriptor=InferenceProviderDescriptor(
            provider_id="anthropic",
            display_name="Anthropic",
            kind=InferenceProviderKind.API,
            protocol=InferenceProtocol.ANTHROPIC_MESSAGES,
            provider_version=PRESET_VERSION,
            default_model=None,
            task_types=(MATERIAL_CLASSIFICATION_TASK,),
        ),
        endpoint="https://api.anthropic.com/v1/messages",
        model_discovery_endpoint="https://api.anthropic.com/v1/models?limit=1000",
        secret_ref="model-api/anthropic",
        development_environment_variable="ANTHROPIC_API_KEY",
    ),
    ModelApiPreset(
        descriptor=InferenceProviderDescriptor(
            provider_id="deepseek",
            display_name="DeepSeek",
            kind=InferenceProviderKind.API,
            protocol=InferenceProtocol.OPENAI_CHAT_COMPLETIONS,
            provider_version=PRESET_VERSION,
            default_model=None,
            task_types=(MATERIAL_CLASSIFICATION_TASK,),
        ),
        endpoint="https://api.deepseek.com/chat/completions",
        model_discovery_endpoint="https://api.deepseek.com/models",
        secret_ref="model-api/deepseek",
        development_environment_variable="DEEPSEEK_API_KEY",
        capabilities=ModelApiCapabilities(
            context_window_tokens=1_000_000,
            max_output_tokens=384_000,
            default_max_output_tokens=32_768,
            reasoning_mode=ModelApiReasoningMode.DISABLED,
        ),
    ),
)


def provider_presets() -> tuple[ModelApiPreset, ...]:
    return _PRESETS


def provider_preset(provider_id: str) -> ModelApiPreset:
    normalized = provider_id.strip()
    for preset in _PRESETS:
        if preset.descriptor.provider_id == normalized:
            return preset
    raise ValueError(f"Unknown model API provider: {provider_id}")
