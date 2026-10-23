"""Submit one trusted strategy generation stage for a PlantingRun."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from verdflow.contracts import (
    JobCommandRef,
    JobInfo,
    JobRetentionClass,
    PLANTING_GENERATE_POINTS_COMMAND,
    PLANTING_GENERATION_COMMAND_SCHEMA,
    PLANTING_GENERATION_COMMAND_VERSION,
)
from verdflow.planting import canonical_json_bytes, semantic_hash
from verdflow.project_store import ProjectStore

from .job_runtime import JobRuntime
from .strategy_access import StrategyExecutionAuthorizer


@dataclass(frozen=True, slots=True)
class PlantingGenerationRequest:
    scene_revision_id: str
    region_set_id: str
    selected_region_ids: tuple[str, ...]
    strategy_package_hash: str
    random_seed: int
    args: dict[str, Any] = field(default_factory=dict)
    excluded_role_keys: tuple[str, ...] = ()


class PlantingGenerationService:
    """Validate stable project references and submit a generation Job."""

    def __init__(
        self,
        store: ProjectStore,
        runtime: JobRuntime,
        authorizer: StrategyExecutionAuthorizer,
    ) -> None:
        self._store = store
        self._runtime = runtime
        self._authorizer = authorizer

    def submit(
        self,
        request: PlantingGenerationRequest,
        *,
        idempotency_key: str | None = None,
    ) -> JobInfo:
        scene = self._store.get_scene_revision(
            self._required_text(request.scene_revision_id, "scene_revision_id")
        )
        region_set = self._store.get_region_set(
            self._required_text(request.region_set_id, "region_set_id")
        )
        if region_set.scene_revision_id != scene.scene_revision_id:
            raise ValueError("Planting RegionSet differs from SceneRevision")
        if (
            not isinstance(request.random_seed, int)
            or isinstance(request.random_seed, bool)
            or request.random_seed < -(2**63)
            or request.random_seed > 2**63 - 1
        ):
            raise ValueError("random_seed must fit a signed 64-bit integer")
        if not isinstance(request.args, dict):
            raise ValueError("args must be a JSON object")
        if not isinstance(request.excluded_role_keys, tuple):
            raise ValueError("excluded_role_keys must be a tuple")
        for role_key in request.excluded_role_keys:
            self._required_text(role_key, "excluded_role_key")
        if len(set(request.excluded_role_keys)) != len(request.excluded_role_keys):
            raise ValueError("excluded_role_keys cannot contain duplicates")

        requested = tuple(
            self._required_text(value, "selected_region_id")
            for value in request.selected_region_ids
        )
        if not requested:
            raise ValueError("selected_region_ids cannot be empty")
        if len(set(requested)) != len(requested):
            raise ValueError("selected_region_ids cannot contain duplicates")
        regions = self._store.list_regions(region_set.region_set_id)
        by_id = {region.region_id: region for region in regions}
        unknown = sorted(set(requested) - set(by_id))
        if unknown:
            raise ValueError(
                "Selected Regions are outside the RegionSet: "
                + ", ".join(unknown[:3])
            )
        ordered_region_ids = [
            region.region_id
            for region in sorted(
                (by_id[region_id] for region_id in requested),
                key=lambda region: (region.ordinal, region.region_id),
            )
        ]

        authorization = self._authorizer.authorize(
            self._required_text(
                request.strategy_package_hash,
                "strategy_package_hash",
            )
        )
        strategy = authorization.strategy
        command = {
            "schema": PLANTING_GENERATION_COMMAND_SCHEMA,
            "version": PLANTING_GENERATION_COMMAND_VERSION,
            "operation": "generate_points",
            "scene_revision_id": scene.scene_revision_id,
            "region_set_id": region_set.region_set_id,
            "selected_region_ids": ordered_region_ids,
            "strategy": {
                "id": strategy.package_id,
                "version": strategy.package_version,
                "package_hash": strategy.package_hash,
            },
            "random_seed": request.random_seed,
            "args": request.args,
            "excluded_role_keys": sorted(set(request.excluded_role_keys)),
        }
        content = canonical_json_bytes(command)
        artifact = self._store.put_bytes(
            content,
            media_type="application/vnd.verdflow.command+json",
            schema_id=PLANTING_GENERATION_COMMAND_SCHEMA,
            schema_version=PLANTING_GENERATION_COMMAND_VERSION,
        )
        default_key = (
            "planting-generate:"
            + semantic_hash(command).removeprefix("sha256:")
        )
        return self._runtime.submit(
            JobCommandRef(
                command_type=PLANTING_GENERATE_POINTS_COMMAND,
                command_version=PLANTING_GENERATION_COMMAND_VERSION,
                artifact_id=artifact.artifact_id,
            ),
            idempotency_key=(idempotency_key or default_key),
            max_attempts=1,
            retention_class=JobRetentionClass.RUN,
        )

    @staticmethod
    def _required_text(value: object, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} is required")
        return value.strip()
