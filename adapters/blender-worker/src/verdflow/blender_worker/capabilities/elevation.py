"""Blender BVH elevation sampling for canonical VerdFlow PointSets."""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from .scene import SOURCE_MEMBER_PROP, load_scene_inputs


ProgressCallback = Callable[[int, int], None]
StageCallback = Callable[[str, float], None]
CancelCheck = Callable[[], None]


@dataclass(frozen=True, slots=True)
class ElevationSamplingResult:
    point_set: dict[str, object]
    metrics: dict[str, object]


def sample_elevation(
    source_path: Path | None,
    points_path: Path,
    *,
    source_members: list[dict[str, object]] | None = None,
    import_settings: dict[str, object] | None = None,
    target_object_names: list[str] | None = None,
    region_source_member_ids: dict[str, str] | None = None,
    direction: str = "Z_DOWN",
    ray_start_offset: float = 500.0,
    ray_distance: float = 1500.0,
    include_hidden: bool = False,
    progress_callback: ProgressCallback | None = None,
    stage_callback: StageCallback | None = None,
    cancel_check: CancelCheck | None = None,
    scene_cache_path: Path | None = None,
) -> ElevationSamplingResult:
    started = time.perf_counter()
    if direction not in {"Z_DOWN", "Z_UP"}:
        raise ValueError("Elevation direction must be Z_DOWN or Z_UP")
    if ray_start_offset <= 0.0 or ray_distance <= 0.0:
        raise ValueError("Elevation ray distances must be positive")
    payload = json.loads(points_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("PointSet input must be a JSON object")
    if (
        payload.get("schema") != "verdflow.points"
        or payload.get("version") != "2"
    ):
        raise ValueError("PointSet input schema is unsupported")
    if payload.get("kind") != "generated":
        raise ValueError("Elevation input must be a generated PointSet")
    if payload.get("offset_applied") is not False:
        raise ValueError("Elevation input must use unshifted coordinates")
    points = payload.get("points")
    if not isinstance(points, list):
        raise ValueError("PointSet input points are required")
    member_by_region = dict(region_source_member_ids or {})
    if not all(
        isinstance(region_id, str)
        and region_id
        and isinstance(member_id, str)
        and member_id
        for region_id, member_id in member_by_region.items()
    ):
        raise ValueError("Region source member mapping is invalid")

    source_load_mode = load_scene_inputs(
        source_path=source_path,
        source_members=source_members,
        import_settings=import_settings,
        scene_cache_path=scene_cache_path,
        stage_callback=stage_callback,
    )
    imported_at = time.perf_counter()
    if stage_callback is not None:
        stage_callback("building_elevation_bvh", 0.25)
    if cancel_check is not None:
        cancel_check()
    targets = _mesh_targets(
        target_object_names=target_object_names,
        include_hidden=include_hidden,
    )
    bvh_targets = _build_bvh_targets(targets)
    bvh_built_at = time.perf_counter()
    if stage_callback is not None:
        stage_callback("sampling_elevation", 0.3)
    if not bvh_targets:
        raise RuntimeError("No mesh targets are available for elevation")

    sampled: list[dict[str, object]] = []
    hit_count = 0
    total = len(points)
    progress_step = max(1, total // 100)
    for index, point in enumerate(points):
        if not isinstance(point, dict):
            raise ValueError("PointSet row must be an object")
        if cancel_check is not None and index % progress_step == 0:
            cancel_check()
        generated = _vector(point, "generated_position", 3)
        source_member_id = None
        if member_by_region:
            region_id = point.get("region_id")
            if not isinstance(region_id, str) or region_id not in member_by_region:
                raise ValueError(
                    "Point Region lacks source member provenance"
                )
            source_member_id = member_by_region[region_id]
        hit = _sample_point(
            generated,
            bvh_targets,
            direction=direction,
            ray_start_offset=ray_start_offset,
            ray_distance=ray_distance,
            source_member_id=source_member_id,
        )
        next_point = dict(point)
        if hit is None:
            next_point["terrain_sample"] = {"hit": False}
            next_point["resolved_position"] = list(generated)
        else:
            hit_count += 1
            terrain_sample: dict[str, object] = {
                "hit": True,
                "position": hit["position"],
                "normal": hit["normal"],
                "target": hit["target"],
                "face_index": hit["face_index"],
            }
            if hit["source_member_id"] is not None:
                terrain_sample["source_member_id"] = hit[
                    "source_member_id"
                ]
            next_point["terrain_sample"] = terrain_sample
            next_point["resolved_position"] = hit["position"]
        sampled.append(next_point)
        if progress_callback is not None and (
            index % progress_step == 0 or index + 1 == total
        ):
            progress_callback(index + 1, total)
    sampled_at = time.perf_counter()

    point_set = {
        **{key: value for key, value in payload.items() if key != "points"},
        "schema": "verdflow.points",
        "version": "2",
        "coordinate_space": "source_scene_world",
        "offset_applied": False,
        "kind": "elevation_resolved",
        "resolution_policy": "sample_hit_else_generated",
        "point_count": len(sampled),
        "points": sampled,
    }
    metrics = {
        "sampler": "blender_bvh_raycast",
        "direction": direction,
        "ray_start_offset": float(ray_start_offset),
        "ray_distance": float(ray_distance),
        "target_object_count": len(targets),
        "source_member_filtering": bool(member_by_region),
        "source_load_mode": source_load_mode,
        "hit_count": hit_count,
        "miss_count": len(sampled) - hit_count,
        "performance": {
            "source_import_seconds": imported_at - started,
            "bvh_build_seconds": bvh_built_at - imported_at,
            "raycast_seconds": sampled_at - bvh_built_at,
            "total_seconds": sampled_at - started,
        },
    }
    return ElevationSamplingResult(point_set=point_set, metrics=metrics)


def _mesh_targets(
    *,
    target_object_names: list[str] | None,
    include_hidden: bool,
) -> list[Any]:
    allowed = set(target_object_names or ())
    targets: list[Any] = []
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        if allowed and obj.name not in allowed:
            continue
        if not include_hidden and (obj.hide_get() or obj.hide_viewport):
            continue
        targets.append(obj)
    return targets


def _build_bvh_targets(
    objects: list[Any],
) -> list[tuple[Any, Any, Any]]:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    targets: list[tuple[Any, Any, Any]] = []
    for obj in objects:
        evaluated = obj.evaluated_get(depsgraph)
        try:
            tree = BVHTree.FromObject(evaluated, depsgraph)
        except Exception:
            tree = None
        if tree is not None:
            targets.append((obj, evaluated, tree))
    return targets


def _sample_point(
    generated: tuple[float, float, float],
    targets: list[tuple[Any, Any, Any]],
    *,
    direction: str,
    ray_start_offset: float,
    ray_distance: float,
    source_member_id: str | None,
) -> dict[str, object] | None:
    if direction == "Z_DOWN":
        origin = Vector(
            (generated[0], generated[1], generated[2] + ray_start_offset)
        )
        world_direction = Vector((0.0, 0.0, -1.0))
    else:
        origin = Vector(
            (generated[0], generated[1], generated[2] - ray_start_offset)
        )
        world_direction = Vector((0.0, 0.0, 1.0))
    best: dict[str, object] | None = None
    best_distance = math.inf
    for obj, evaluated, tree in targets:
        if (
            source_member_id is not None
            and obj.get(SOURCE_MEMBER_PROP) != source_member_id
        ):
            continue
        matrix = evaluated.matrix_world
        inverse = matrix.inverted()
        local_origin = inverse @ origin
        local_direction = (
            inverse.to_3x3() @ world_direction
        ).normalized()
        location, normal, face_index, _ = tree.ray_cast(
            local_origin,
            local_direction,
        )
        if location is None:
            continue
        world_location = matrix @ location
        world_distance = (world_location - origin).length
        if world_distance > ray_distance or world_distance >= best_distance:
            continue
        if normal is None:
            world_normal = Vector((0.0, 0.0, 1.0))
        else:
            world_normal = (
                matrix.to_3x3().inverted().transposed() @ normal
            ).normalized()
        best_distance = world_distance
        best = {
            "position": [
                float(world_location.x),
                float(world_location.y),
                float(world_location.z),
            ],
            "normal": [
                float(world_normal.x),
                float(world_normal.y),
                float(world_normal.z),
            ],
            "target": obj.name,
            "source_member_id": obj.get(SOURCE_MEMBER_PROP),
            "face_index": (
                int(face_index) if face_index is not None else -1
            ),
        }
    return best


def _vector(
    payload: dict[str, object],
    field: str,
    length: int,
) -> tuple[float, ...]:
    value = payload.get(field)
    if (
        not isinstance(value, list)
        or len(value) != length
        or not all(
            isinstance(item, (int, float))
            and not isinstance(item, bool)
            and math.isfinite(float(item))
            for item in value
        )
    ):
        raise ValueError(f"{field} is invalid")
    return tuple(float(item) for item in value)
