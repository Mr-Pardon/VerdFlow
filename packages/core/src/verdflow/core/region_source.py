"""Submit a source that directly produces canonical Regions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from verdflow.contracts import (
    JobCommandRef,
    JobInfo,
    JobRetentionClass,
    REGION_SOURCE_COMMAND_SCHEMA,
    REGION_SOURCE_COMMAND_VERSION,
    SourceKind,
)
from verdflow.planting import canonical_json_bytes, semantic_hash
from verdflow.project_store import ProjectStore

from .job_runtime import JobRuntime
from .region_providers import RegionSetProducerDefinition


@dataclass(frozen=True, slots=True)
class RegionSourceProviderDefinition(RegionSetProducerDefinition):
    """Registered source facts -> initial SceneRevision and RegionSet."""


@dataclass(frozen=True, slots=True)
class RegionSourceRequest:
    source_id: str
    provider_id: str
    options: dict[str, Any] = field(default_factory=dict)


class RegionSourceService:
    """Freeze a provider-neutral direct Region source command."""

    def __init__(
        self,
        store: ProjectStore,
        runtime: JobRuntime,
        providers: tuple[RegionSourceProviderDefinition, ...],
    ) -> None:
        self._store = store
        self._runtime = runtime
        self._providers: dict[str, RegionSourceProviderDefinition] = {}
        for provider in providers:
            provider.validate()
            if provider.provider_id in self._providers:
                raise ValueError(
                    f"Duplicate Region source provider: {provider.provider_id}"
                )
            self._providers[provider.provider_id] = provider
        if not self._providers:
            raise ValueError("At least one Region source provider is required")

    def submit(
        self,
        request: RegionSourceRequest,
        *,
        idempotency_key: str | None = None,
    ) -> JobInfo:
        source_id = self._required_text(request.source_id, "source_id")
        provider_id = self._required_text(request.provider_id, "provider_id")
        try:
            provider = self._providers[provider_id]
        except KeyError as error:
            raise ValueError(
                f"Region source provider is not registered: {provider_id}"
            ) from error
        if not isinstance(request.options, dict):
            raise ValueError("options must be a JSON object")
        self._reject_local_paths(request.options)

        source = self._store.get_source(source_id)
        if source.kind is not SourceKind.ASSEMBLY:
            raise ValueError("Region source input must be a Source Assembly")
        if not self._store.list_source_assembly_members(source.source_id):
            raise ValueError("Region source Assembly has no members")
        workflow = self._store.project_workflow_state()
        if workflow.source_id not in {None, source.source_id}:
            raise ValueError(
                "Project source is fixed; create another project for a new source"
            )
        command = {
            "schema": REGION_SOURCE_COMMAND_SCHEMA,
            "version": REGION_SOURCE_COMMAND_VERSION,
            "operation": "prepare_regions",
            "provider": {
                "id": provider.provider_id,
                "version": provider.provider_version,
            },
            "source_id": source.source_id,
            "source_artifact_id": source.artifact_id,
            "options": request.options,
        }
        command_artifact = self._store.put_bytes(
            canonical_json_bytes(command),
            media_type="application/vnd.verdflow.command+json",
            schema_id=REGION_SOURCE_COMMAND_SCHEMA,
            schema_version=REGION_SOURCE_COMMAND_VERSION,
        )
        default_key = (
            "region-source:" + semantic_hash(command).removeprefix("sha256:")
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
    def _reject_local_paths(cls, value: object, path: str = "options") -> None:
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
