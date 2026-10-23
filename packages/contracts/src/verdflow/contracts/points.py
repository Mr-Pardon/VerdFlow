"""Canonical planting run and point set summaries."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PointSetKind(StrEnum):
    GENERATED = "generated"
    ELEVATION_RESOLVED = "elevation_resolved"


@dataclass(frozen=True, slots=True)
class PlantingRunInfo:
    planting_run_id: str
    scene_revision_id: str
    region_set_id: str
    producer_job_id: str
    strategy_id: str
    strategy_version: str
    strategy_package_hash: str
    generator_id: str
    generator_version: str
    random_seed: int
    input_hash: str
    created_at: str


@dataclass(frozen=True, slots=True)
class PointSetInfo:
    point_set_id: str
    planting_run_id: str
    parent_point_set_id: str | None
    producer_job_id: str
    kind: PointSetKind
    producer_id: str
    producer_version: str
    input_hash: str
    coordinate_frame_id: str
    point_artifact_id: str
    resolution_policy: str
    point_count: int
    sampled_count: int
    hit_count: int
    miss_count: int
    min_x: float | None
    min_y: float | None
    min_z: float | None
    max_x: float | None
    max_y: float | None
    max_z: float | None
    created_at: str


@dataclass(frozen=True, slots=True)
class PointRoleSummary:
    role_key: str
    point_count: int
