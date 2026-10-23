"""Canonical region set summaries shared across VerdFlow boundaries."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RegionSummary:
    region_id: str
    ordinal: int
    label: str
    tags: str
    classification: str | None
    strategy_hint: str | None
    footprint_area_xy: float | None
    boundary_perimeter_xy: float | None
    min_x: float
    min_y: float
    min_z: float
    max_x: float
    max_y: float
    max_z: float
    metrics_json: str
    source_member_id: str | None = None


@dataclass(frozen=True, slots=True)
class RegionSetInfo:
    region_set_id: str
    scene_revision_id: str
    producer_job_id: str
    extractor_id: str
    extractor_version: str
    input_hash: str
    coordinate_frame_id: str
    geometry_artifact_id: str
    metrics_artifact_id: str
    region_count: int
    created_at: str
