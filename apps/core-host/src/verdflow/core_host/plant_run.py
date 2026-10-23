"""Explicit four-stage planting run orchestration for one Core Host."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Protocol

from verdflow.contracts import (
    AssetAssignmentSetInfo,
    ExportRecordInfo,
    JobInfo,
    PointSetInfo,
)
from verdflow.core import (
    AssetAssignmentRequest,
    ElevationSamplingRequest,
    OutputExportRequest,
    PlantingGenerationRequest,
)


@dataclass(frozen=True, slots=True)
class PlantingGenerationOutcome:
    job: JobInfo
    point_set: PointSetInfo


@dataclass(frozen=True, slots=True)
class ElevationSamplingOutcome:
    job: JobInfo
    point_set: PointSetInfo


@dataclass(frozen=True, slots=True)
class AssetAssignmentOutcome:
    job: JobInfo
    assignments: AssetAssignmentSetInfo


@dataclass(frozen=True, slots=True)
class OutputExportOutcome:
    job: JobInfo
    export: ExportRecordInfo


@dataclass(frozen=True, slots=True)
class GenerateAndAssignRequest:
    """Generate target-neutral points, then resolve target assets."""

    scene_revision_id: str
    region_set_id: str
    selected_region_ids: tuple[str, ...]
    strategy_package_hash: str
    random_seed: int
    target_type: str
    catalog_id: str | None
    profile_id: str | None = None
    assignment_random_seed: int | None = None
    generation_args: dict[str, Any] = field(default_factory=dict)
    binding_overrides: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GenerateAndAssignOutcome:
    planting_run_id: str
    generation: PlantingGenerationOutcome
    assignment: AssetAssignmentOutcome


@dataclass(frozen=True, slots=True)
class ResolveElevationAndExportRequest:
    """Resolve terrain height and export an already assigned point set."""

    generated_point_set_id: str
    asset_assignment_set_id: str
    elevation_provider_id: str
    export_provider_id: str
    offset: tuple[float, float, float]
    elevation_options: dict[str, Any] = field(default_factory=dict)
    require_terrain_hit: bool = False


@dataclass(frozen=True, slots=True)
class ResolveElevationAndExportOutcome:
    elevation: ElevationSamplingOutcome
    output: OutputExportOutcome


@dataclass(frozen=True, slots=True)
class PlantRunRequest:
    """Exact user choices required by the first end-to-end planting flow."""

    scene_revision_id: str
    region_set_id: str
    selected_region_ids: tuple[str, ...]
    strategy_package_hash: str
    random_seed: int
    elevation_provider_id: str
    target_type: str
    catalog_id: str
    export_provider_id: str
    offset: tuple[float, float, float]
    profile_id: str | None = None
    assignment_random_seed: int | None = None
    generation_args: dict[str, Any] = field(default_factory=dict)
    elevation_options: dict[str, Any] = field(default_factory=dict)
    binding_overrides: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PlantRunTimeouts:
    generation_seconds: float | None = None
    elevation_seconds: float = 1800.0
    assignment_seconds: float = 300.0
    export_seconds: float = 300.0

    def validate(self) -> None:
        for name, value in (
            ("generation_seconds", self.generation_seconds),
            ("elevation_seconds", self.elevation_seconds),
            ("assignment_seconds", self.assignment_seconds),
            ("export_seconds", self.export_seconds),
        ):
            if name == "generation_seconds" and value is None:
                continue
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")


@dataclass(frozen=True, slots=True)
class PlantRunOutcome:
    planting_run_id: str
    generation: PlantingGenerationOutcome
    elevation: ElevationSamplingOutcome
    assignment: AssetAssignmentOutcome
    output: OutputExportOutcome


class PlantRunStageHost(Protocol):
    """Minimal synchronous stage surface consumed by the coordinator."""

    def freeze_role_assignments(self, profile_id: str | None, overrides: dict[str, Any]) -> dict[str, Any]: ...

    def freeze_profile(self, profile_id: str | None) -> dict[str, Any] | None: ...

    def freeze_generation_args(self, profile_id: str | None, args: dict[str, Any]) -> dict[str, Any]: ...

    def generate_points(
        self,
        request: PlantingGenerationRequest,
        *,
        timeout_seconds: float | None,
    ) -> PlantingGenerationOutcome: ...

    def sample_elevation(
        self,
        request: ElevationSamplingRequest,
        *,
        timeout_seconds: float,
    ) -> ElevationSamplingOutcome: ...

    def assign_assets(
        self,
        request: AssetAssignmentRequest,
        *,
        timeout_seconds: float,
    ) -> AssetAssignmentOutcome: ...

    def export_output(
        self,
        request: OutputExportRequest,
        *,
        timeout_seconds: float,
    ) -> OutputExportOutcome: ...


class PlantRunCoordinator:
    """Sequence stable stage Jobs without introducing a general DAG engine."""

    def __init__(self, stages: PlantRunStageHost) -> None:
        self._stages = stages

    def execute(
        self,
        request: PlantRunRequest,
        *,
        timeouts: PlantRunTimeouts | None = None,
    ) -> PlantRunOutcome:
        limits = timeouts or PlantRunTimeouts()
        limits.validate()
        prepared = self.generate_and_assign(
            GenerateAndAssignRequest(
                scene_revision_id=request.scene_revision_id,
                region_set_id=request.region_set_id,
                selected_region_ids=request.selected_region_ids,
                strategy_package_hash=request.strategy_package_hash,
                random_seed=request.random_seed,
                target_type=request.target_type,
                catalog_id=request.catalog_id,
                profile_id=request.profile_id,
                assignment_random_seed=request.assignment_random_seed,
                generation_args=request.generation_args,
                binding_overrides=request.binding_overrides,
            ),
            timeouts=limits,
        )
        resolved = self.resolve_elevation_and_export(
            ResolveElevationAndExportRequest(
                generated_point_set_id=(
                    prepared.generation.point_set.point_set_id
                ),
                asset_assignment_set_id=(
                    prepared.assignment.assignments.asset_assignment_set_id
                ),
                elevation_provider_id=request.elevation_provider_id,
                export_provider_id=request.export_provider_id,
                offset=request.offset,
                elevation_options=request.elevation_options,
            ),
            timeouts=limits,
        )
        return PlantRunOutcome(
            planting_run_id=prepared.planting_run_id,
            generation=prepared.generation,
            elevation=resolved.elevation,
            assignment=prepared.assignment,
            output=resolved.output,
        )

    def generate_and_assign(
        self,
        request: GenerateAndAssignRequest,
        *,
        timeouts: PlantRunTimeouts | None = None,
    ) -> GenerateAndAssignOutcome:
        limits = timeouts or PlantRunTimeouts()
        limits.validate()
        assignment_seed = (
            request.random_seed
            if request.assignment_random_seed is None
            else request.assignment_random_seed
        )
        bindings = self._stages.freeze_role_assignments(request.profile_id, request.binding_overrides)
        profile_snapshot = self._stages.freeze_profile(request.profile_id)
        generation = self._stages.generate_points(
            PlantingGenerationRequest(
                scene_revision_id=request.scene_revision_id,
                region_set_id=request.region_set_id,
                selected_region_ids=request.selected_region_ids,
                strategy_package_hash=request.strategy_package_hash,
                random_seed=request.random_seed,
                args=self._stages.freeze_generation_args(request.profile_id, request.generation_args),
                excluded_role_keys=tuple(sorted(key for key, value in bindings.items() if value.get("enabled", True) is False)),
            ),
            timeout_seconds=limits.generation_seconds,
        )
        assignment = self._stages.assign_assets(
            AssetAssignmentRequest(
                point_set_id=generation.point_set.point_set_id,
                target_type=request.target_type,
                catalog_id=request.catalog_id,
                random_seed=assignment_seed,
                profile_id=request.profile_id,
                overrides=bindings,
                profile_snapshot=profile_snapshot,
            ),
            timeout_seconds=limits.assignment_seconds,
        )
        return GenerateAndAssignOutcome(
            planting_run_id=generation.point_set.planting_run_id,
            generation=generation,
            assignment=assignment,
        )

    def resolve_elevation_and_export(
        self,
        request: ResolveElevationAndExportRequest,
        *,
        timeouts: PlantRunTimeouts | None = None,
    ) -> ResolveElevationAndExportOutcome:
        limits = timeouts or PlantRunTimeouts()
        limits.validate()
        elevation = self._stages.sample_elevation(
            ElevationSamplingRequest(
                parent_point_set_id=request.generated_point_set_id,
                provider_id=request.elevation_provider_id,
                options=request.elevation_options,
            ),
            timeout_seconds=limits.elevation_seconds,
        )
        output = self._stages.export_output(
            OutputExportRequest(
                point_set_id=elevation.point_set.point_set_id,
                asset_assignment_set_id=request.asset_assignment_set_id,
                provider_id=request.export_provider_id,
                offset=request.offset,
                require_terrain_hit=request.require_terrain_hit,
            ),
            timeout_seconds=limits.export_seconds,
        )
        return ResolveElevationAndExportOutcome(
            elevation=elevation,
            output=output,
        )
