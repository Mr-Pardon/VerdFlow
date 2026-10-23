"""Immutable DTOs owned by the optional Studio query service."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Bounds3D:
    min_x: float
    min_y: float
    min_z: float
    max_x: float
    max_y: float
    max_z: float


@dataclass(frozen=True, slots=True)
class RegionClassificationSummary:
    classification: str | None
    region_count: int
    footprint_area_xy: float


@dataclass(frozen=True, slots=True)
class RegionViewerManifest:
    region_set_id: str
    scene_revision_id: str
    coordinate_frame_id: str
    region_count: int
    bounds: Bounds3D | None
    classifications: tuple[RegionClassificationSummary, ...]


@dataclass(frozen=True, slots=True)
class RegionViewerItem:
    region_id: str
    ordinal: int
    label: str
    classification: str | None
    strategy_hint: str | None
    footprint_area_xy: float | None
    bounds: Bounds3D
    footprint_boundary_xyz: tuple[tuple[tuple[float, float, float], ...], ...]


@dataclass(frozen=True, slots=True)
class RegionViewportChunk:
    items: tuple[RegionViewerItem, ...]
    next_after_ordinal: int | None
    truncated: bool


@dataclass(frozen=True, slots=True)
class PointLegendItem:
    key: str
    point_count: int
    display_name: str | None = None
    reference_radius_m: float | None = None
    reference_height_m: float | None = None
    max_resolved_radius_m: float | None = None


@dataclass(frozen=True, slots=True)
class PointReviewManifest:
    point_set_id: str
    assignment_set_id: str
    planting_run_id: str
    region_set_id: str
    coordinate_frame_id: str
    kind: str
    target_type: str
    point_count: int
    bounds: Bounds3D | None
    roles: tuple[PointLegendItem, ...]
    assets: tuple[PointLegendItem, ...]
    max_resolved_radius_m: float = 0.0


@dataclass(frozen=True, slots=True)
class PointReviewItem:
    point_id: str
    ordinal: int
    region_id: str
    role_key: str
    position: tuple[float, float, float]
    asset_id: str
    target_scale_xyz: tuple[float, float, float]
    group_id: str | None = None


@dataclass(frozen=True, slots=True)
class PointViewportChunk:
    items: tuple[PointReviewItem, ...]
    next_after_ordinal: int | None
    truncated: bool
