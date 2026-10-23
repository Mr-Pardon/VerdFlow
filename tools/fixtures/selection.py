"""Generic helpers for selecting private validation fixture subsets."""

from __future__ import annotations

from typing import Any


def point_matches_member(point: dict[str, Any], member_key: str) -> bool:
    normalized_key = member_key.upper()
    hit_object = str(point.get("hit_object", "")).upper()
    region_id = str(point.get("region_id", "")).upper()
    return normalized_key in hit_object or region_id.startswith(
        f"{normalized_key}_"
    )


def point_inside(
    point: dict[str, Any],
    bounds: tuple[float, float, float, float],
) -> bool:
    try:
        x = float(point["source_x"])
        y = float(point["source_y"])
    except (KeyError, TypeError, ValueError):
        return False
    min_x, min_y, max_x, max_y = bounds
    return min_x <= x <= max_x and min_y <= y <= max_y


def bbox_inside(
    bbox: Any,
    bounds: tuple[float, float, float, float],
) -> bool:
    if not isinstance(bbox, list) or len(bbox) < 6:
        return False
    min_x, min_y, max_x, max_y = bounds
    return (
        float(bbox[0]) >= min_x
        and float(bbox[1]) >= min_y
        and float(bbox[3]) <= max_x
        and float(bbox[4]) <= max_y
    )
