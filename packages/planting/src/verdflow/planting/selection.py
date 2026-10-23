"""Deterministic role and asset selection without resampling or reassignment."""

from __future__ import annotations

import copy
from typing import Any

from .canonical import semantic_hash
from .errors import PlantingDocumentError
from .validation import (
    validate_assignment_set,
    validate_persisted_assignment_set_summary,
    validate_persisted_point_set_summary,
    validate_point_set_v2,
    with_semantic_hash,
)


def role_enabled(assignment: dict[str, Any]) -> bool:
    value = assignment.get("enabled", True)
    if not isinstance(value, bool):
        raise PlantingDocumentError("Role enabled must be a boolean")
    return value


def normalize_asset_selection(value: object) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or not value:
        raise PlantingDocumentError("Select at least one output asset")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise PlantingDocumentError("Output asset identifiers must be non-empty strings")
    if len(set(value)) != len(value):
        raise PlantingDocumentError("Output asset identifiers must be unique")
    return tuple(sorted(value))


def filter_point_roles(point_set: dict[str, Any], excluded_roles: tuple[str, ...]) -> dict[str, Any]:
    validate_point_set_v2(point_set)
    if not excluded_roles:
        return point_set
    excluded = set(excluded_roles)
    points = [point for point in point_set["points"] if point["role_key"] not in excluded]
    if not points:
        raise PlantingDocumentError("Enabled roles produced no planting points")
    return {**point_set, "points": points, "point_count": len(points)}


def select_output_assets(
    point_set: dict[str, Any],
    assignment_set: dict[str, Any],
    selected_asset_ids: tuple[str, ...] | None,
    *,
    require_terrain_hit: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    selected = normalize_asset_selection(selected_asset_ids)
    if selected is None and not require_terrain_hit:
        return point_set, assignment_set
    validate_point_set_v2(point_set)
    validate_assignment_set(assignment_set)
    return _select_output_assets(point_set, assignment_set, selected, require_terrain_hit)


def select_persisted_output_assets(
    point_set: dict[str, Any],
    assignment_set: dict[str, Any],
    selected_asset_ids: tuple[str, ...] | None,
    *,
    expected_point_count: int,
    expected_assignment_hash: str,
    require_terrain_hit: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Select accepted Core artifacts without repeating their full validation."""
    validate_persisted_point_set_summary(
        point_set, expected_point_count=expected_point_count,
    )
    validate_persisted_assignment_set_summary(
        assignment_set,
        expected_point_count=expected_point_count,
        expected_semantic_hash=expected_assignment_hash,
    )
    selected = normalize_asset_selection(selected_asset_ids)
    return _select_output_assets(point_set, assignment_set, selected, require_terrain_hit)


def _select_output_assets(
    point_set: dict[str, Any],
    assignment_set: dict[str, Any],
    selected: tuple[str, ...] | None,
    require_terrain_hit: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if selected is None and not require_terrain_hit:
        return point_set, assignment_set
    available = {row["asset_id"] for row in assignment_set["assignments"]}
    unknown = set(selected or ()) - available
    if unknown:
        raise PlantingDocumentError("Output references unknown assets: " + ", ".join(sorted(unknown)))
    eligible = {point["id"] for point in point_set["points"]
                if not require_terrain_hit or point.get("terrain_sample", {}).get("hit") is True}
    rows = [row for row in assignment_set["assignments"]
            if (selected is None or row["asset_id"] in selected) and row["point_id"] in eligible]
    ids = {row["point_id"] for row in rows}
    points = [point for point in point_set["points"] if point["id"] in ids]
    if not points or len(points) != len(rows):
        raise PlantingDocumentError("Output selection has no points or mismatched assignments")
    if len(points) == len(point_set["points"]) and len(rows) == len(assignment_set["assignments"]):
        return point_set, assignment_set
    filtered_points = {**point_set, "points": points, "point_count": len(points)}
    filtered_assignments = copy.copy(assignment_set)
    filtered_assignments.pop("semantic_hash", None)
    filtered_assignments.update(
        assignments=rows, point_count=len(rows), point_set_hash=semantic_hash(filtered_points),
    )
    return filtered_points, with_semantic_hash(filtered_assignments)
