"""Submit one provider-neutral elevation sampling stage."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from verdflow.contracts import (
    ELEVATION_SAMPLING_COMMAND_SCHEMA,
    ELEVATION_SAMPLING_COMMAND_VERSION,
    JobCommandRef,
    JobInfo,
    JobRetentionClass,
    PointSetKind,
)
from verdflow.planting import canonical_json_bytes, semantic_hash
from verdflow.project_store import ProjectStore

from .job_runtime import JobRuntime


@dataclass(frozen=True, slots=True)
class ElevationProviderDefinition:
    """Stable execution identity registered by a composition root."""

    provider_id: str
    provider_version: str
    command_type: str
    command_version: str
    max_attempts: int = 2

    def validate(self) -> None:
        for field_name in (
            "provider_id",
            "provider_version",
            "command_type",
            "command_version",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} is required")
            if value != value.strip():
                raise ValueError(f"{field_name} cannot contain outer whitespace")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")


@dataclass(frozen=True, slots=True)
class ElevationSamplingRequest:
    parent_point_set_id: str
    provider_id: str
    options: dict[str, Any] = field(default_factory=dict)


class ElevationSamplingService:
    """Validate PointSet lineage and submit to one registered provider."""

    def __init__(
        self,
        store: ProjectStore,
        runtime: JobRuntime,
        providers: tuple[ElevationProviderDefinition, ...],
    ) -> None:
        self._store = store
        self._runtime = runtime
        self._providers: dict[str, ElevationProviderDefinition] = {}
        for provider in providers:
            provider.validate()
            key = provider.provider_id.strip()
            if key in self._providers:
                raise ValueError(f"Duplicate elevation provider: {key}")
            self._providers[key] = provider
        if not self._providers:
            raise ValueError("At least one elevation provider is required")

    def submit(
        self,
        request: ElevationSamplingRequest,
        *,
        idempotency_key: str | None = None,
    ) -> JobInfo:
        parent_id = self._required_text(
            request.parent_point_set_id,
            "parent_point_set_id",
        )
        provider_id = self._required_text(request.provider_id, "provider_id")
        try:
            provider = self._providers[provider_id]
        except KeyError as error:
            raise ValueError(
                f"Elevation provider is not registered: {provider_id}"
            ) from error
        if not isinstance(request.options, dict):
            raise ValueError("options must be a JSON object")
        self._reject_local_paths(request.options)

        parent = self._store.get_point_set(parent_id)
        if parent.kind is not PointSetKind.GENERATED:
            raise ValueError("Elevation input must be a generated PointSet")
        run = self._store.get_planting_run(parent.planting_run_id)
        scene = self._store.get_scene_revision(run.scene_revision_id)
        source = self._store.get_source(scene.source_id)
        if parent.coordinate_frame_id != scene.coordinate_frame_id:
            raise ValueError("PointSet coordinate frame differs from SceneRevision")

        command = {
            "schema": ELEVATION_SAMPLING_COMMAND_SCHEMA,
            "version": ELEVATION_SAMPLING_COMMAND_VERSION,
            "operation": "sample_elevation",
            "provider": {
                "id": provider.provider_id,
                "version": provider.provider_version,
            },
            "source_id": source.source_id,
            "source_artifact_id": source.artifact_id,
            "scene_revision_id": scene.scene_revision_id,
            "coordinate_frame_id": scene.coordinate_frame_id,
            "parent_point_set_id": parent.point_set_id,
            "points_artifact_id": parent.point_artifact_id,
            "options": request.options,
        }
        content = canonical_json_bytes(command)
        artifact = self._store.put_bytes(
            content,
            media_type="application/vnd.verdflow.command+json",
            schema_id=ELEVATION_SAMPLING_COMMAND_SCHEMA,
            schema_version=ELEVATION_SAMPLING_COMMAND_VERSION,
        )
        default_key = (
            "elevation-sample:"
            + semantic_hash(command).removeprefix("sha256:")
        )
        return self._runtime.submit(
            JobCommandRef(
                command_type=provider.command_type,
                command_version=provider.command_version,
                artifact_id=artifact.artifact_id,
            ),
            idempotency_key=(idempotency_key or default_key),
            max_attempts=provider.max_attempts,
            retention_class=JobRetentionClass.RUN,
        )

    @staticmethod
    def _required_text(value: object, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} is required")
        return value.strip()

    @classmethod
    def _reject_local_paths(cls, value: object, path: str = "options") -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if isinstance(key, str) and key.lower().endswith(
                    ("_path", "_paths")
                ):
                    raise ValueError(
                        f"{path}.{key} cannot contain a local path"
                    )
                cls._reject_local_paths(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                cls._reject_local_paths(item, f"{path}[{index}]")
