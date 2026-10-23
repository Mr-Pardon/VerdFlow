"""Project-level planting composition contracts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PlantingCompositionInfo:
    """The single current multi-batch planting result for one RegionSet."""

    composition_id: str
    project_id: str
    scene_revision_id: str
    region_set_id: str
    generation: int
    batch_count: int
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class PlantingBatchInfo:
    """One accepted Plant Run and its exact occupied Regions."""

    batch_id: str
    composition_id: str
    ordinal: int
    plant_run_execution_id: str
    generated_point_set_id: str
    asset_assignment_set_id: str
    selected_region_ids: tuple[str, ...]
    selected_asset_ids: tuple[str, ...] | None
    created_at: str
