"""Adapter-local immutable configuration and protocol response values."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from verdflow.contracts import InferenceProviderDescriptor, ModelTokenUsage


class ModelApiReasoningMode(StrEnum):
    PROVIDER_DEFAULT = "provider_default"
    DISABLED = "disabled"
    ENABLED = "enabled"


@dataclass(frozen=True, slots=True)
class ModelApiCapabilities:
    context_window_tokens: int | None = None
    max_output_tokens: int | None = None
    default_max_output_tokens: int = 8192
    reasoning_mode: ModelApiReasoningMode = ModelApiReasoningMode.PROVIDER_DEFAULT


@dataclass(frozen=True, slots=True)
class ModelApiProviderConfig:
    descriptor: InferenceProviderDescriptor
    endpoint: str
    secret_ref: str
    model_discovery_endpoint: str | None = None
    development_environment_variable: str | None = None
    capabilities: ModelApiCapabilities = field(default_factory=ModelApiCapabilities)
    builtin: bool = False


@dataclass(frozen=True, slots=True)
class ModelApiPreset(ModelApiProviderConfig):
    builtin: bool = True


@dataclass(frozen=True, slots=True)
class ModelApiModelInfo:
    model_id: str
    display_name: str | None = None
    owned_by: str | None = None


@dataclass(frozen=True, slots=True)
class ProtocolResponse:
    request_id: str | None
    output: dict[str, Any]
    usage: ModelTokenUsage
    finish_reason: str | None = None


@dataclass(frozen=True, slots=True)
class MaterialAnalysisCallPlan:
    provider_id: str
    model: str
    material_count: int
    estimated_input_tokens: int
    estimated_output_tokens: int
    requested_max_output_tokens: int
    provider_context_window_tokens: int | None
    provider_max_output_tokens: int | None
    warnings: tuple[str, ...]
