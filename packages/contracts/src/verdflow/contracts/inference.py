"""Versioned structured inference contracts shared by VerdFlow adapters."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


MODEL_ANALYSIS_COMMAND_SCHEMA = "verdflow.model-analysis.command"
MODEL_ANALYSIS_COMMAND_VERSION = "1"
MODEL_ANALYSIS_RESULT_SCHEMA = "verdflow.model-analysis.result"
MODEL_ANALYSIS_RESULT_VERSION = "1"
MATERIAL_CLASSIFICATION_TASK = "material_classification.v1"
MATERIAL_CLASSIFICATION_PROMPT_POLICY_VERSION = "1"
MATERIAL_CLASSIFICATION_CANDIDATE_SCHEMA = (
    "verdflow.material-classification-candidate"
)
MATERIAL_CLASSIFICATION_CANDIDATE_VERSION = "1"


class InferenceProviderKind(StrEnum):
    API = "api"
    AGENT = "agent"


class InferenceProtocol(StrEnum):
    OPENAI_RESPONSES = "openai-responses"
    ANTHROPIC_MESSAGES = "anthropic-messages"
    OPENAI_CHAT_COMPLETIONS = "openai-chat-completions"
    CODEX_SDK = "codex-sdk"


class MaterialClassification(StrEnum):
    GENERAL_GREEN = "general_green"
    STREET_TREE_GREEN = "street_tree_green"
    NOT_GREEN = "not_green"


class MaterialConfirmationMode(StrEnum):
    MANUAL = "manual"
    AUTOMATIC = "automatic"


@dataclass(frozen=True, slots=True)
class InferenceProviderDescriptor:
    provider_id: str
    display_name: str
    kind: InferenceProviderKind
    protocol: InferenceProtocol
    provider_version: str
    default_model: str | None
    task_types: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ModelTokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None

    def to_document(self) -> dict[str, int | None]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass(frozen=True, slots=True)
class ModelInvocationInfo:
    provider_id: str
    provider_version: str
    protocol: InferenceProtocol
    model: str
    prompt_policy_version: str
    request_id: str | None
    usage: ModelTokenUsage

    def to_document(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "provider_version": self.provider_version,
            "protocol": self.protocol.value,
            "model": self.model,
            "prompt_policy_version": self.prompt_policy_version,
            "request_id": self.request_id,
            "usage": self.usage.to_document(),
        }


@dataclass(frozen=True, slots=True)
class MaterialClassificationItem:
    material_name: str
    classification: MaterialClassification
    confidence: float
    evidence: tuple[str, ...]

    def to_document(self) -> dict[str, Any]:
        return {
            "material_name": self.material_name,
            "classification": self.classification.value,
            "confidence": self.confidence,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True, slots=True)
class MaterialClassificationCandidate:
    task_id: str
    task_type: str
    input_ref: str
    invocation: ModelInvocationInfo
    materials: tuple[MaterialClassificationItem, ...]
    warnings: tuple[str, ...]

    def to_document(self) -> dict[str, Any]:
        return {
            "schema": MATERIAL_CLASSIFICATION_CANDIDATE_SCHEMA,
            "version": MATERIAL_CLASSIFICATION_CANDIDATE_VERSION,
            "task_id": self.task_id,
            "task_type": self.task_type,
            "input_ref": self.input_ref,
            "invocation": self.invocation.to_document(),
            "materials": [item.to_document() for item in self.materials],
            "warnings": list(self.warnings),
        }


def material_classification_candidate_from_document(
    document: Any,
) -> MaterialClassificationCandidate:
    if not isinstance(document, dict):
        raise ValueError("Material classification candidate must be an object")
    if document.get("schema") != MATERIAL_CLASSIFICATION_CANDIDATE_SCHEMA:
        raise ValueError("Material classification candidate schema is unsupported")
    if str(document.get("version")) != MATERIAL_CLASSIFICATION_CANDIDATE_VERSION:
        raise ValueError("Material classification candidate version is unsupported")
    invocation = _required_object(document, "invocation")
    usage = _required_object(invocation, "usage")
    rows = document.get("materials")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Material classification candidate materials are required")
    materials: list[MaterialClassificationItem] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Material classification candidate item must be an object")
        name = _required_text(row, "material_name")
        if name in seen:
            raise ValueError(f"Material classification candidate duplicates: {name}")
        seen.add(name)
        confidence = row.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise ValueError(f"Material classification confidence is invalid: {name}")
        confidence_value = float(confidence)
        if confidence_value < 0 or confidence_value > 1:
            raise ValueError(f"Material classification confidence is invalid: {name}")
        evidence = row.get("evidence")
        if not isinstance(evidence, list) or not all(
            isinstance(value, str) and value.strip() for value in evidence
        ):
            raise ValueError(f"Material classification evidence is invalid: {name}")
        materials.append(
            MaterialClassificationItem(
                material_name=name,
                classification=MaterialClassification(
                    _required_text(row, "classification")
                ),
                confidence=confidence_value,
                evidence=tuple(value.strip() for value in evidence),
            )
        )
    warnings = document.get("warnings")
    if not isinstance(warnings, list) or not all(
        isinstance(value, str) and value.strip() for value in warnings
    ):
        raise ValueError("Material classification candidate warnings are invalid")
    return MaterialClassificationCandidate(
        task_id=_required_text(document, "task_id"),
        task_type=_required_text(document, "task_type"),
        input_ref=_required_text(document, "input_ref"),
        invocation=ModelInvocationInfo(
            provider_id=_required_text(invocation, "provider_id"),
            provider_version=_required_text(invocation, "provider_version"),
            protocol=InferenceProtocol(_required_text(invocation, "protocol")),
            model=_required_text(invocation, "model"),
            prompt_policy_version=_required_text(
                invocation,
                "prompt_policy_version",
            ),
            request_id=_optional_text(invocation.get("request_id"), "request_id"),
            usage=ModelTokenUsage(
                input_tokens=_optional_int(usage.get("input_tokens"), "input_tokens"),
                output_tokens=_optional_int(
                    usage.get("output_tokens"),
                    "output_tokens",
                ),
                total_tokens=_optional_int(usage.get("total_tokens"), "total_tokens"),
            ),
        ),
        materials=tuple(materials),
        warnings=tuple(value.strip() for value in warnings),
    )


def _required_object(document: dict[str, Any], field: str) -> dict[str, Any]:
    value = document.get(field)
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return value


def _required_text(document: dict[str, Any], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value.strip()


def _optional_text(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be text or null")
    return value.strip()


def _optional_int(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer or null")
    return value
