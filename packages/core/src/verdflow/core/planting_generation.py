"""Submit one trusted strategy generation stage for a PlantingRun."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any

from verdflow.contracts import (
    JobCommandRef,
    JobInfo,
    JobRetentionClass,
    PLANTING_GENERATE_POINTS_COMMAND,
    PLANTING_GENERATION_COMMAND_SCHEMA,
    PLANTING_GENERATION_COMMAND_VERSION,
    PlantingInputRef,
)
from verdflow.planting import (
    canonical_json_bytes,
    semantic_hash,
    validate_path_document,
)
from verdflow.project_store import ProjectStore

from .job_runtime import JobRuntime
from .strategy_access import StrategyExecutionAuthorizer


@dataclass(frozen=True, slots=True)
class PlantingGenerationRequest:
    scene_revision_id: str
    region_set_id: str | None
    selected_region_ids: tuple[str, ...]
    strategy_package_hash: str
    random_seed: int
    args: dict[str, Any] = field(default_factory=dict)
    excluded_role_keys: tuple[str, ...] = ()
    input_ref: PlantingInputRef | None = None


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
        reference = request.input_ref
        if reference is not None and reference.kind == "polyline":
            if request.region_set_id is not None or request.selected_region_ids:
                raise ValueError("路径输入不能同时携带区域引用")
            region_set = self._store.get_path_set(reference.set_id)
        else:
            if reference is not None:
                if (
                    reference.kind != "polygon"
                    or reference.set_id != request.region_set_id
                    or reference.selected_ids != request.selected_region_ids
                ):
                    raise ValueError("种植输入引用与区域选择不一致")
            region_set = self._store.get_region_set(
                self._required_text(request.region_set_id, "region_set_id")
            )
        if region_set.scene_revision_id != scene.scene_revision_id:
            raise ValueError("Planting input set differs from SceneRevision")
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

        path_input = reference is not None and reference.kind == "polyline"
        requested = tuple(
            self._required_text(value, "selected_region_id")
            for value in (
                reference.selected_ids if path_input else request.selected_region_ids
            )
        )
        if not requested:
            raise ValueError("selected_region_ids cannot be empty")
        if len(set(requested)) != len(requested):
            raise ValueError("selected_region_ids cannot contain duplicates")
        if path_input:
            with self._store.open_artifact(region_set.geometry_artifact_id) as stream:
                paths = validate_path_document(json.load(stream))["paths"]
            by_id = {path["id"]: ordinal for ordinal, path in enumerate(paths)}
        else:
            regions = self._store.list_regions(region_set.region_set_id)
            by_id = {region.region_id: region for region in regions}
        unknown = sorted(set(requested) - set(by_id))
        if unknown:
            raise ValueError(
                "Selected geometries are outside the planting input set: "
                + ", ".join(unknown[:3])
            )
        ordered_region_ids = (
            sorted(requested, key=lambda key: by_id[key])
            if path_input
            else [
                region.region_id
                for region in sorted(
                    (by_id[region_id] for region_id in requested),
                    key=lambda region: (region.ordinal, region.region_id),
                )
            ]
        )

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
            "strategy": {
                "id": strategy.package_id,
                "version": strategy.package_version,
                "package_hash": strategy.package_hash,
            },
            "random_seed": request.random_seed,
            "args": request.args,
            "excluded_role_keys": sorted(set(request.excluded_role_keys)),
        }
        if path_input:
            command["input"] = {
                "kind": "polyline",
                "set_id": reference.set_id,
                "selected_ids": ordered_region_ids,
            }
        else:
            command.update(
                region_set_id=region_set.region_set_id,
                selected_region_ids=ordered_region_ids,
            )
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
