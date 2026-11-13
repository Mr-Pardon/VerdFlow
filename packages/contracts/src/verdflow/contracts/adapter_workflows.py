"""Versioned values for registered recipes and frozen sampling decisions."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Literal


CapabilityKind = Literal["region_source", "region_extraction", "elevation", "output"]
SamplingSourceKind = Literal["source", "scene", "session"]


@dataclass(frozen=True, slots=True)
class CapabilityRef:
    kind: CapabilityKind
    provider_id: str
    provider_version: str
    command_type: str
    command_version: str

    def validate(self) -> None:
        if self.kind not in {"region_source", "region_extraction", "elevation", "output"}:
            raise ValueError("Unsupported capability kind")
        for name in ("provider_id", "provider_version", "command_type", "command_version"):
            _text(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class InputRecipe:
    recipe_id: str
    version: str
    adapter_id: str
    preparation: CapabilityRef
    sampling: CapabilityRef
    geometry_kinds: tuple[str, ...]
    sampling_source_kind: SamplingSourceKind
    configuration_schema: str
    failure_policy: str
    legacy_importer_ids: tuple[str, ...] = ()
    source_member_kind: str | None = None

    def validate(self) -> None:
        for name in ("recipe_id", "version", "adapter_id", "configuration_schema", "failure_policy"):
            _text(getattr(self, name), name)
        self.preparation.validate()
        self.sampling.validate()
        if self.preparation.kind not in {"region_source", "region_extraction"}:
            raise ValueError("Recipe preparation capability has the wrong kind")
        if self.sampling.kind != "elevation":
            raise ValueError("Recipe sampling capability has the wrong kind")
        if not self.geometry_kinds or set(self.geometry_kinds) - {"polygon", "polyline"}:
            raise ValueError("Recipe geometry kinds are invalid")
        if self.sampling_source_kind not in {"source", "scene", "session"}:
            raise ValueError("Recipe sampling source kind is invalid")
        if (self.sampling_source_kind == "source") != (self.source_member_kind is not None):
            raise ValueError("Source recipe requires an explicit member kind")


@dataclass(frozen=True, slots=True)
class SamplingSourceRef:
    kind: SamplingSourceKind
    source_id: str
    artifact_id: str
    content_hash: str
    coordinate_frame_id: str
    scene_revision_id: str | None = None
    assembly_source_id: str | None = None
    source_member_id: str | None = None
    connector_session_id: str | None = None
    operation_id: str | None = None
    generation_token: int | None = None

    def validate(self) -> None:
        if self.kind not in {"source", "scene", "session"}:
            raise ValueError("Unsupported sampling source kind")
        for name in ("source_id", "artifact_id", "content_hash", "coordinate_frame_id"):
            _text(getattr(self, name), name)
        digest = self.content_hash.removeprefix("sha256:")
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("Sampling source requires a content hash")
        if (self.assembly_source_id is None) != (self.source_member_id is None):
            raise ValueError("Sampling member requires its Assembly")
        if self.kind in {"scene", "session"}:
            _text(self.scene_revision_id, "scene_revision_id")
        if self.kind == "session":
            _text(self.connector_session_id, "connector_session_id")
            _text(self.operation_id, "operation_id")
            if type(self.generation_token) is not int or self.generation_token < 1:
                raise ValueError("Live sampling generation token is invalid")
        elif any(value is not None for value in (
            self.connector_session_id, self.operation_id, self.generation_token,
        )):
            raise ValueError("Non-live sampling cannot carry session authorization")


@dataclass(frozen=True, slots=True)
class SamplingBinding:
    provider: CapabilityRef
    source: SamplingSourceRef
    configuration_schema: str
    configuration: dict[str, Any]
    failure_policy: str
    alignment: str = "canonical-unshifted-meters"

    def validate(self) -> None:
        self.provider.validate()
        self.source.validate()
        if self.provider.kind != "elevation":
            raise ValueError("Sampling binding requires an elevation capability")
        _text(self.configuration_schema, "configuration_schema")
        _text(self.failure_policy, "failure_policy")
        if self.alignment != "canonical-unshifted-meters":
            raise ValueError("Sampling requires aligned, unshifted metric coordinates")
        if not isinstance(self.configuration, dict):
            raise ValueError("Sampling configuration must be an object")
        _safe_configuration(self.configuration)

    def to_document(self) -> dict[str, Any]:
        self.validate()
        return {"schema": "verdflow.sampling-binding", "version": "1", **asdict(self)}

    @classmethod
    def from_document(cls, value: dict[str, Any]) -> SamplingBinding:
        fields = _payload(value, "verdflow.sampling-binding")
        fields["provider"] = CapabilityRef(**fields["provider"])
        fields["source"] = SamplingSourceRef(**fields["source"])
        result = cls(**fields)
        result.validate()
        return result


@dataclass(frozen=True, slots=True)
class ResolvedPlantWorkflow:
    recipe_id: str
    recipe_version: str
    recipe_hash: str
    scene_revision_id: str
    sampling: SamplingBinding
    output: CapabilityRef
    target_type: str

    @property
    def elevation_provider_id(self) -> str:
        return self.sampling.provider.provider_id

    @property
    def export_provider_id(self) -> str:
        return self.output.provider_id

    def validate(self) -> None:
        for name in ("recipe_id", "recipe_version", "recipe_hash", "scene_revision_id", "target_type"):
            _text(getattr(self, name), name)
        self.sampling.validate()
        self.output.validate()
        if self.output.kind != "output":
            raise ValueError("Workflow output capability has the wrong kind")

    def to_document(self) -> dict[str, Any]:
        self.validate()
        return {"schema": "verdflow.resolved-plant-workflow", "version": "1",
                **asdict(self), "sampling": self.sampling.to_document()}

    @classmethod
    def from_document(cls, value: dict[str, Any]) -> ResolvedPlantWorkflow:
        fields = _payload(value, "verdflow.resolved-plant-workflow")
        fields["sampling"] = SamplingBinding.from_document(fields["sampling"])
        fields["output"] = CapabilityRef(**fields["output"])
        result = cls(**fields)
        result.validate()
        return result


def _payload(value: object, schema: str) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema") != schema or value.get("version") != "1":
        raise ValueError(f"Unsupported {schema} schema")
    return {key: item for key, item in value.items() if key not in {"schema", "version"}}


def _text(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{name} must be non-empty text without outer whitespace")


def _safe_configuration(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or key.lower().endswith(("_path", "_paths")) or key.lower() in {"password", "secret", "token", "credentials"}:
                raise ValueError("Sampling configuration cannot contain paths or credentials")
            _safe_configuration(item)
    elif isinstance(value, list):
        for item in value:
            _safe_configuration(item)
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Sampling configuration requires finite numbers")
    elif value is not None and not isinstance(value, (str, bool, int, float)):
        raise ValueError("Sampling configuration contains a non-JSON value")
