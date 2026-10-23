from __future__ import annotations

from typing import Any


def generate(context: dict[str, Any]) -> dict[str, Any]:
    """Replace this deterministic example with the intended spatial algorithm."""

    strategy = dict(context.get("strategy") or {})
    regions = sorted(
        list(context.get("selected_regions") or []),
        key=lambda item: str(item.get("id") or item.get("name") or ""),
    )
    points: list[dict[str, Any]] = []
    for index, region in enumerate(regions, start=1):
        region_id = str(region.get("id") or f"region_{index:04d}")
        # Implement bounded, hole-aware generation here.
        del region_id

    return {
        "schema": "verdflow.points",
        "version": "2",
        "strategy": {
            "strategy_id": strategy.get("strategy_id", "example_strategy"),
            "strategy_version": strategy.get("strategy_version", "1.0.0"),
        },
        "coordinate_space": "source_scene_world",
        "offset_applied": False,
        "kind": "generated",
        "resolution_policy": "generated_position",
        "point_count": len(points),
        "points": points,
    }

