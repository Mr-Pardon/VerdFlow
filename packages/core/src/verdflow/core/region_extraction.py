"""Submit deterministic Region extraction to a registered provider."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from verdflow.contracts import (
    JobCommandRef,
    JobInfo,
    JobRetentionClass,
    REGION_EXTRACTION_COMMAND_SCHEMA,
    REGION_EXTRACTION_COMMAND_VERSION,
)
from verdflow.planting import canonical_json_bytes, semantic_hash
from verdflow.project_store import ProjectStore

from .job_runtime import JobRuntime
from .region_providers import RegionSetProducerDefinition


@dataclass(frozen=True, slots=True)
class RegionExtractionProviderDefinition(RegionSetProducerDefinition):
    """Existing SceneRevision plus rules -> RegionSet processor."""


@dataclass(frozen=True, slots=True)
class RegionExtractionRequest:
    scene_revision_id: str
    provider_id: str
    rule_pack: dict[str, Any]
    allow_contains: bool = False
    import_settings: dict[str, Any] = field(default_factory=dict)


class RegionExtractionService:
    """Freeze an exact rule pack and submit one provider extraction Job."""

    def __init__(
        self,
        store: ProjectStore,
        runtime: JobRuntime,
        providers: tuple[RegionExtractionProviderDefinition, ...],
    ) -> None:
        self._store = store
        self._runtime = runtime
        self._providers: dict[str, RegionExtractionProviderDefinition] = {}
        for provider in providers:
            provider.validate()
            if provider.provider_id in self._providers:
                raise ValueError(
                    f"Duplicate Region extraction provider: {provider.provider_id}"
                )
            self._providers[provider.provider_id] = provider
        if not self._providers:
            raise ValueError("At least one Region extraction provider is required")

    def submit(
        self,
        request: RegionExtractionRequest,
        *,
        idempotency_key: str | None = None,
    ) -> JobInfo:
        scene_revision_id = self._required_text(
            request.scene_revision_id,
            "scene_revision_id",
        )
        provider_id = self._required_text(request.provider_id, "provider_id")
        try:
            provider = self._providers[provider_id]
        except KeyError as error:
            raise ValueError(
                f"Region extraction provider is not registered: {provider_id}"
            ) from error
        if not isinstance(request.rule_pack, dict):
            raise ValueError("rule_pack must be a JSON object")
        if request.rule_pack.get("schema") != "verdflow.region-rule-pack":
            raise ValueError("rule_pack schema is unsupported")
        if str(request.rule_pack.get("version")) != "1":
            raise ValueError("rule_pack version is unsupported")
        rules = request.rule_pack.get("rules")
        if not isinstance(rules, list) or not rules:
            raise ValueError("rule_pack rules must be a non-empty array")
        if not isinstance(request.allow_contains, bool):
            raise ValueError("allow_contains must be a boolean")
        if not isinstance(request.import_settings, dict):
            raise ValueError("import_settings must be a JSON object")
        self._reject_local_paths(request.import_settings)

        scene = self._store.get_scene_revision(scene_revision_id)
        source = self._store.get_source(scene.source_id)
        rule_pack_bytes = canonical_json_bytes(request.rule_pack)
        rule_pack_artifact = self._store.put_bytes(
            rule_pack_bytes,
            media_type="application/vnd.verdflow.rule-pack+json",
            schema_id="verdflow.region-rule-pack",
            schema_version="1",
        )
        command = {
            "schema": REGION_EXTRACTION_COMMAND_SCHEMA,
            "version": REGION_EXTRACTION_COMMAND_VERSION,
            "operation": "extract_regions",
            "provider": {
                "id": provider.provider_id,
                "version": provider.provider_version,
            },
            "source_id": source.source_id,
            "source_artifact_id": source.artifact_id,
            "scene_revision_id": scene.scene_revision_id,
            "coordinate_frame_id": scene.coordinate_frame_id,
            "rule_pack_artifact_id": rule_pack_artifact.artifact_id,
            "allow_contains": request.allow_contains,
            "import_settings": request.import_settings,
        }
        command_artifact = self._store.put_bytes(
            canonical_json_bytes(command),
            media_type="application/vnd.verdflow.command+json",
            schema_id=REGION_EXTRACTION_COMMAND_SCHEMA,
            schema_version=REGION_EXTRACTION_COMMAND_VERSION,
        )
        default_key = (
            "region-extract:" + semantic_hash(command).removeprefix("sha256:")
        )
        return self._runtime.submit(
            JobCommandRef(
                command_type=provider.command_type,
                command_version=provider.command_version,
                artifact_id=command_artifact.artifact_id,
            ),
            idempotency_key=idempotency_key or default_key,
            max_attempts=provider.max_attempts,
            retention_class=JobRetentionClass.RUN,
        )

    @staticmethod
    def _required_text(value: object, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} is required")
        return value.strip()

    @classmethod
    def _reject_local_paths(cls, value: object, path: str = "import_settings") -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if isinstance(key, str) and key.lower().endswith(
                    ("_path", "_paths")
                ):
                    raise ValueError(f"{path}.{key} cannot contain a local path")
                cls._reject_local_paths(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                cls._reject_local_paths(item, f"{path}[{index}]")
