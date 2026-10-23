"""Strategy Worker adapter for lawn feature-group generation."""

from __future__ import annotations

import hashlib
import math
import random
from typing import Any

from algorithm import PlantingParameters, RegionShape, generate_composition


DEFAULT_PARAMETERS = PlantingParameters()
PARAMETER_KEYS = frozenset(DEFAULT_PARAMETERS.__dataclass_fields__)
PARAMETER_BOUNDS = {
    "groundcover_spacing_m": (0.5, 4.0),
    "feature_group_density": (0.0, 4.0),
    "feature_anchor_probability": (0.0, 1.0),
    "feature_group_min_count": (0.0, 50.0),
    "feature_group_max_count": (0.0, 50.0),
}


def generate(context: dict[str, Any]) -> dict[str, Any]:
    """Generate a target-neutral PointSet from selected Region documents."""
    strategy = dict(context.get("strategy") or {})
    parameters = _effective_parameters(strategy, dict(context.get("args") or {}))
    seed = int(context.get("seed", 17))
    regions = sorted(
        list(context.get("selected_regions") or []),
        key=lambda item: str(item.get("id") or item.get("name") or ""),
    )

    points: list[dict[str, Any]] = []
    for region_index, payload in enumerate(regions, start=1):
        region, z = _region_shape(payload, fallback_id=f"region_{region_index:04d}")
        result = generate_composition(region, parameters, seed=seed)
        role_indexes: dict[str, int] = {}
        for composition_point in result.points:
            role_index = role_indexes.get(composition_point.role_key, 0) + 1
            role_indexes[composition_point.role_key] = role_index
            yaw = _point_rng(
                seed,
                region.region_id,
                composition_point.role_key,
                composition_point.x,
                composition_point.y,
            ).uniform(-math.pi, math.pi)
            position = [composition_point.x, composition_point.y, z]
            points.append(
                {
                    "id": (
                        f"{region.region_id}:{composition_point.role_key}:"
                        f"{role_index:06d}"
                    ),
                    "region_id": region.region_id,
                    "role_key": composition_point.role_key,
                    "generated_position": position,
                    "terrain_sample": None,
                    "resolved_position": position.copy(),
                    "rotation_quaternion_xyzw": [
                        0.0,
                        0.0,
                        math.sin(yaw / 2.0),
                        math.cos(yaw / 2.0),
                    ],
                    "group_id": composition_point.group_id,
                }
            )

    return {
        "schema": "verdflow.points",
        "version": "2",
        "strategy": {
            "strategy_id": strategy.get("strategy_id", "lawn_feature_groups"),
            "strategy_version": strategy.get("strategy_version", "1.0.0"),
        },
        "coordinate_space": "source_scene_world",
        "offset_applied": False,
        "kind": "generated",
        "resolution_policy": "generated_position",
        "point_count": len(points),
        "points": points,
    }


def _effective_parameters(
    strategy: dict[str, Any],
    args: dict[str, Any],
) -> PlantingParameters:
    contract = strategy.get("generation_parameters") or []
    if not isinstance(contract, list):
        raise ValueError("generation_parameters must be an array")
    defaults = {
        str(item.get("key")): item.get("default")
        for item in contract
        if isinstance(item, dict)
    }
    overrides = args.get("strategy_parameters") or {}
    if not isinstance(overrides, dict):
        raise ValueError("strategy_parameters must be an object")
    unknown = sorted(
        (set(defaults) - PARAMETER_KEYS)
        | (set(overrides) - set(defaults))
    )
    if unknown:
        raise ValueError(f"Unknown strategy parameter: {unknown[0]}")
    values = {key: getattr(DEFAULT_PARAMETERS, key) for key in PARAMETER_KEYS}
    values.update(defaults)
    values.update(overrides)
    validated: dict[str, float] = {}
    for key, raw_value in values.items():
        if isinstance(raw_value, bool):
            raise ValueError(f"Strategy parameter {key} must be a number")
        try:
            value = float(raw_value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"Strategy parameter {key} must be a number") from error
        minimum, maximum = PARAMETER_BOUNDS[key]
        if not math.isfinite(value) or not minimum <= value <= maximum:
            raise ValueError(
                f"Strategy parameter {key} must be between {minimum} and {maximum}"
            )
        validated[key] = value
    return PlantingParameters(**validated)


def _region_shape(
    payload: dict[str, Any],
    *,
    fallback_id: str,
) -> tuple[RegionShape, float]:
    region_id = str(payload.get("id") or payload.get("name") or fallback_id)
    rings = payload.get("rings")
    if isinstance(rings, list) and rings:
        outer_candidates = [
            _clean_ring(item.get("loop") or [])
            for item in rings
            if str(item.get("role", "outer")) != "hole"
        ]
        holes = [
            _clean_ring(item.get("loop") or [])
            for item in rings
            if str(item.get("role", "outer")) == "hole"
        ]
    else:
        raw = list(payload.get("footprint_boundary_xyz") or payload.get("loops") or [])
        outer_candidates = [_clean_ring(raw[0])] if raw else []
        holes = [_clean_ring(ring) for ring in raw[1:]]
    outer_candidates = [ring for ring in outer_candidates if len(ring) >= 3]
    holes = [ring for ring in holes if len(ring) >= 3]
    if len(outer_candidates) != 1:
        raise ValueError(f"Region {region_id!r} requires exactly one outer ring")
    outer = outer_candidates[0]
    z = sum(point[2] for point in outer) / len(outer)
    return (
        RegionShape(
            region_id=region_id,
            outer=tuple((point[0], point[1]) for point in outer),
            holes=tuple(
                tuple((point[0], point[1]) for point in hole)
                for hole in holes
            ),
        ),
        z,
    )


def _clean_ring(value: list[Any]) -> list[tuple[float, float, float]]:
    points = [
        (
            float(point[0]),
            float(point[1]),
            float(point[2] if len(point) > 2 else 0.0),
        )
        for point in value
        if isinstance(point, list | tuple) and len(point) >= 2
    ]
    if len(points) >= 2 and points[0] == points[-1]:
        points.pop()
    return points


def _point_rng(
    seed: int,
    region_id: str,
    role_key: str,
    x: float,
    y: float,
) -> random.Random:
    payload = f"{seed}\0{region_id}\0{role_key}\0{x:.8f}\0{y:.8f}".encode()
    return random.Random(int.from_bytes(hashlib.sha256(payload).digest()[:8], "big"))
