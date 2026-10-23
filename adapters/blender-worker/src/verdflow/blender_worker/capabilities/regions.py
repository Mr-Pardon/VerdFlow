"""Blender-backed rule extraction and connected region export."""

from __future__ import annotations

import json
import math
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import bpy
from mathutils import Vector

from ..algorithms import (
    connected_face_islands,
    dominant_projected_boundary_loops,
    first_matching_rule,
    polygon_area_xy,
    polygon_perimeter_xy,
    sanitize_name,
    validate_rule_pack,
)
from .scene import (
    SOURCE_MEMBER_PROP,
    load_scene_inputs,
    material_texture_infos,
    object_bbox_world,
    source_roots_for_inputs,
)


REGION_PROP = "verdflow_region"
REGION_COLLECTION = "VerdFlow Regions"
StageCallback = Callable[[str, float], None]
CancelCheck = Callable[[], None]
XY_AREA_EPSILON = 1e-9


@dataclass(frozen=True, slots=True)
class _MaterialSlotFacts:
    material_name: str
    source_texture_paths: tuple[str, ...]
    rule_id: str | None
    evidence: tuple[str, ...]


def boundary_loops_world(
    obj: Any,
    face_indices: list[int] | None = None,
) -> list[list[list[float]]]:
    mesh = obj.data
    indices = (
        face_indices
        if face_indices is not None
        else [polygon.index for polygon in mesh.polygons]
    )
    face_edges = {
        index: list(mesh.polygons[index].edge_keys) for index in indices
    }
    world_vertices: dict[int, tuple[float, float, float]] = {}
    face_points: dict[int, list[tuple[float, float, float]]] = {}
    for index in indices:
        points = []
        for vertex_index in mesh.polygons[index].vertices:
            if vertex_index not in world_vertices:
                world_vertices[vertex_index] = tuple(
                    obj.matrix_world @ mesh.vertices[vertex_index].co
                )
            points.append(world_vertices[vertex_index])
        face_points[index] = points
    loops = dominant_projected_boundary_loops(
        face_edges,
        face_points,
        area_epsilon=XY_AREA_EPSILON,
    )
    result: list[list[list[float]]] = []
    for loop in loops:
        coordinates = []
        for vertex_index in loop:
            world = world_vertices[vertex_index]
            coordinates.append([float(value) for value in world])
        coordinates.append(coordinates[0])
        result.append(coordinates)
    result.sort(
        key=lambda loop: polygon_area_xy(
            [tuple(point) for point in loop[:-1]]
        ),
        reverse=True,
    )
    return result


def _footprint_area_xy(boundary_loops: list[list[list[float]]]) -> float:
    loop_areas = [
        polygon_area_xy([tuple(point) for point in loop[:-1]])
        for loop in boundary_loops
    ]
    return (
        max(0.0, loop_areas[0] - math.fsum(loop_areas[1:]))
        if loop_areas
        else 0.0
    )


def region_to_dict(
    obj: Any,
    boundary_loops: list[list[list[float]]] | None = None,
) -> dict[str, object]:
    if boundary_loops is None:
        boundary_loops = boundary_loops_world(obj)
    payload: dict[str, object] = {
        "id": obj.get("region_id", obj.name),
        "name": obj.name,
        "label": obj.get("region_label", ""),
        "tags": obj.get("region_tags", ""),
        "region_type": obj.get("region_type", "region"),
        "source_object": obj.get("source_object", ""),
        "source_member_id": obj.get(SOURCE_MEMBER_PROP),
        "coordinate_space": "source_scene_world",
        "offset_applied": False,
        "bbox": object_bbox_world(obj),
        "footprint_boundary_xyz": boundary_loops,
        "rings": [
            {
                "index": index,
                "role": "outer" if index == 0 else "hole",
                "loop": loop,
            }
            for index, loop in enumerate(boundary_loops)
        ],
        "mesh": obj.name,
    }
    for key in (
        "confidence",
        "needs_review",
        "extraction_method",
        "rule_id",
        "source_materials",
        "source_texture_paths",
        "evidence",
        "greenbelt_type",
        "strategy_hint",
        "classification_source",
        "street_tree_confidence",
        "classification_evidence",
    ):
        if key not in obj:
            continue
        value = obj.get(key)
        if isinstance(value, str) and value.startswith(("[", "{")):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                pass
        payload[key] = value
    return payload


def mesh_from_face_indices(
    source_obj: Any,
    face_indices: list[int],
    name: str,
) -> Any:
    source_mesh = source_obj.data
    world = source_obj.matrix_world
    vertex_map: dict[int, int] = {}
    vertices = []
    faces = []
    material_indices: list[int | None] = []
    materials = []
    material_map: dict[str, int] = {}
    slot_materials = tuple(slot.material for slot in source_obj.material_slots)
    for face_index in face_indices:
        polygon = source_mesh.polygons[face_index]
        face = []
        for vertex_index in polygon.vertices:
            if vertex_index not in vertex_map:
                vertex_map[vertex_index] = len(vertices)
                vertices.append(
                    world @ source_mesh.vertices[vertex_index].co
                )
            face.append(vertex_map[vertex_index])
        faces.append(face)
        material = (
            slot_materials[polygon.material_index]
            if 0 <= polygon.material_index < len(slot_materials)
            else None
        )
        if material is None:
            material_indices.append(None)
        else:
            if material.name not in material_map:
                material_map[material.name] = len(materials)
                materials.append(material)
            material_indices.append(material_map[material.name])

    mesh = bpy.data.meshes.new(f"{name}_mesh")
    mesh.from_pydata([tuple(value) for value in vertices], [], faces)
    mesh.update()
    for material in materials:
        mesh.materials.append(material)
    for polygon, material_index in zip(
        mesh.polygons,
        material_indices,
        strict=True,
    ):
        if material_index is not None:
            polygon.material_index = material_index
    return bpy.data.objects.new(name, mesh)


def object_geometry_metrics(
    obj: Any,
    boundary_loops: list[list[list[float]]] | None = None,
) -> dict[str, object]:
    mesh = obj.data
    world = obj.matrix_world
    area_3d = 0.0
    normal_z_values: list[float] = []
    weighted_normal = Vector((0.0, 0.0, 0.0))
    for polygon in mesh.polygons:
        vertices = [world @ mesh.vertices[index].co for index in polygon.vertices]
        if len(vertices) < 3:
            continue
        origin = vertices[0]
        for index in range(1, len(vertices) - 1):
            cross = (vertices[index] - origin).cross(
                vertices[index + 1] - origin
            )
            triangle_area = cross.length * 0.5
            area_3d += triangle_area
            if cross.length > 1e-9:
                normal = cross.normalized()
                weighted_normal += normal * triangle_area
                normal_z_values.append(abs(normal.z))
    loops = (
        boundary_loops
        if boundary_loops is not None
        else boundary_loops_world(obj)
    )
    footprint_area = _footprint_area_xy(loops)
    perimeter = sum(
        polygon_perimeter_xy([tuple(point) for point in loop[:-1]])
        for loop in loops
    )
    bbox = object_bbox_world(obj)
    size_x = abs(bbox[3] - bbox[0])
    size_y = abs(bbox[4] - bbox[1])
    short_xy = min(size_x, size_y)
    normal = (
        weighted_normal.normalized()
        if weighted_normal.length > 1e-9
        else Vector((0.0, 0.0, 0.0))
    )
    horizontal_ratio = (
        sum(value >= 0.85 for value in normal_z_values)
        / len(normal_z_values)
        if normal_z_values
        else None
    )
    return {
        "id": obj.get("region_id", obj.name),
        "name": obj.name,
        "source_member_id": obj.get(SOURCE_MEMBER_PROP),
        "face_count": len(mesh.polygons),
        "vertex_count": len(mesh.vertices),
        "bbox": bbox,
        "bbox_size": [
            size_x,
            size_y,
            abs(bbox[5] - bbox[2]),
        ],
        "xy_aspect_ratio": (
            max(size_x, size_y) / short_xy if short_xy > 1e-9 else None
        ),
        "area_3d": area_3d,
        "footprint_area_xy": footprint_area,
        "boundary_perimeter_xy": perimeter,
        "boundary_loop_count": len(loops),
        "weighted_normal": [normal.x, normal.y, normal.z],
        "horizontal_face_ratio": horizontal_ratio,
        "is_near_horizontal": (
            horizontal_ratio is not None and horizontal_ratio >= 0.8
        ),
    }


def _material_slot_facts(
    material: Any,
    *,
    object_name: str,
    rules: list[dict[str, object]],
    source_roots: tuple[Path, ...],
) -> _MaterialSlotFacts:
    material_name = material.name if material else "__NO_MATERIAL__"
    textures = material_texture_infos(material, source_roots)
    texture_values = tuple(
        value
        for texture in textures
        for value in (
            texture.get("image", ""),
            texture.get("source_relative_path", ""),
        )
        if value
    )
    match = first_matching_rule(
        rules,
        material_name=material_name,
        object_name=object_name,
        texture_values=texture_values,
    )
    return _MaterialSlotFacts(
        material_name=material_name,
        source_texture_paths=tuple(
            str(texture["source_relative_path"])
            for texture in textures
            if texture.get("source_relative_path")
        ),
        rule_id=match[0] if match else None,
        evidence=tuple(match[1]) if match else (),
    )


def _polygon_material_facts(
    polygon: Any,
    slot_facts: tuple[_MaterialSlotFacts, ...],
    fallback: _MaterialSlotFacts,
) -> _MaterialSlotFacts:
    material_index = int(polygon.material_index)
    if 0 <= material_index < len(slot_facts):
        return slot_facts[material_index]
    return fallback


def extract_regions(
    source_path: Path | None,
    rule_pack: dict[str, object],
    *,
    source_name: str,
    source_members: list[dict[str, object]] | None = None,
    collection_name: str = "VerdFlow Extracted Regions",
    allow_contains: bool = False,
    import_settings: dict[str, object] | None = None,
    scene_cache_path: Path | None = None,
    stage_callback: StageCallback | None = None,
    cancel_check: CancelCheck | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
    started = time.perf_counter()
    validate_rule_pack(rule_pack, allow_contains=allow_contains)
    source_load_mode = load_scene_inputs(
        source_path=source_path,
        source_members=source_members,
        import_settings=import_settings,
        scene_cache_path=scene_cache_path,
        stage_callback=stage_callback,
    )
    loaded_at = time.perf_counter()
    if cancel_check is not None:
        cancel_check()
    source_roots = source_roots_for_inputs(source_path, source_members)
    rules = [
        rule for rule in rule_pack["rules"] if isinstance(rule, dict)
    ]
    collection = bpy.data.collections.new(collection_name)
    bpy.context.scene.collection.children.link(collection)
    regions = []
    metrics = []
    counter = 1
    source_objects = [
        obj for obj in bpy.data.objects if obj.type == "MESH"
    ]
    matches_by_object = []
    object_total = max(1, len(source_objects))
    progress_step = max(1, object_total // 20)
    if stage_callback is not None:
        stage_callback("scanning_material_faces", 0.25)
    for object_index, obj in enumerate(source_objects):
        if cancel_check is not None:
            cancel_check()
        matches_by_rule: dict[str, list[int]] = defaultdict(list)
        evidence_by_rule: dict[str, set[str]] = defaultdict(set)
        slot_facts = tuple(
            _material_slot_facts(
                slot.material,
                object_name=obj.name,
                rules=rules,
                source_roots=source_roots,
            )
            for slot in obj.material_slots
        )
        fallback = _material_slot_facts(
            None,
            object_name=obj.name,
            rules=rules,
            source_roots=source_roots,
        )
        for polygon in obj.data.polygons:
            facts = _polygon_material_facts(polygon, slot_facts, fallback)
            if facts.rule_id is None:
                continue
            matches_by_rule[facts.rule_id].append(polygon.index)
            evidence_by_rule[facts.rule_id].update(facts.evidence)
        matches_by_object.append(
            (
                obj,
                matches_by_rule,
                evidence_by_rule,
                slot_facts,
                fallback,
            )
        )
        if stage_callback is not None and (
            (object_index + 1) % progress_step == 0
            or object_index + 1 == object_total
        ):
            stage_callback(
                "scanning_material_faces",
                0.25 + 0.25 * (object_index + 1) / object_total,
            )

    scanned_at = time.perf_counter()
    if stage_callback is not None:
        stage_callback("finding_region_islands", 0.5)
    islands_to_build = []
    for object_index, (
        obj,
        matches_by_rule,
        evidence_by_rule,
        slot_facts,
        fallback,
    ) in enumerate(matches_by_object):
        if cancel_check is not None:
            cancel_check()
        for rule in rules:
            rule_id = str(rule["id"])
            face_indices = matches_by_rule.get(rule_id, [])
            if not face_indices:
                continue
            face_edges = {
                index: list(obj.data.polygons[index].edge_keys)
                for index in face_indices
            }
            for island in connected_face_islands(face_edges):
                islands_to_build.append(
                    (
                        obj,
                        rule,
                        rule_id,
                        evidence_by_rule,
                        slot_facts,
                        fallback,
                        island,
                    )
                )
        if stage_callback is not None and (
            (object_index + 1) % progress_step == 0
            or object_index + 1 == object_total
        ):
            stage_callback(
                "finding_region_islands",
                0.5 + 0.1 * (object_index + 1) / object_total,
            )

    islands_found_at = time.perf_counter()
    island_total = max(1, len(islands_to_build))
    island_progress_step = max(1, island_total // 20)
    if stage_callback is not None:
        stage_callback("building_region_geometry", 0.6)
    skipped_candidates = []
    for island_index, (
        obj,
        rule,
        rule_id,
        evidence_by_rule,
        slot_facts,
        fallback,
        island,
    ) in enumerate(islands_to_build):
        if cancel_check is not None and island_index % island_progress_step == 0:
            cancel_check()
        prefix = sanitize_name(
            str(rule.get("name_prefix") or rule.get("label") or "region")
        )
        region_name = f"{prefix}_{counter:04d}"
        counter += 1
        boundary_loops = boundary_loops_world(obj, island)
        footprint_area = _footprint_area_xy(boundary_loops)
        if footprint_area <= XY_AREA_EPSILON:
            if len(skipped_candidates) < 25:
                skipped_candidates.append(
                    {
                        "id": region_name,
                        "source_object": obj.name,
                        "face_count": len(island),
                        "reason": "non_positive_xy_footprint",
                    }
                )
            if stage_callback is not None and (
                (island_index + 1) % island_progress_step == 0
                or island_index + 1 == island_total
            ):
                stage_callback(
                    "building_region_geometry",
                    0.6 + 0.22 * (island_index + 1) / island_total,
                )
            continue
        region_obj = mesh_from_face_indices(obj, island, region_name)
        collection.objects.link(region_obj)
        region_obj[REGION_PROP] = True
        region_obj["region_id"] = region_name
        region_obj["region_type"] = "rule_copy"
        region_obj["source_object"] = obj.name
        source_member_id = obj.get(SOURCE_MEMBER_PROP)
        if source_member_id:
            region_obj[SOURCE_MEMBER_PROP] = source_member_id
        region_obj["region_label"] = str(rule.get("label", ""))
        region_obj["region_tags"] = str(rule.get("tags", ""))
        region_obj["confidence"] = str(rule.get("confidence", "medium"))
        region_obj["needs_review"] = bool(rule.get("needs_review", False))
        region_obj["extraction_method"] = "rule_pack_copy"
        region_obj["rule_id"] = rule_id
        region_obj["evidence"] = json.dumps(
            sorted(set(evidence_by_rule[rule_id]))
        )
        island_facts = [
            _polygon_material_facts(
                obj.data.polygons[index],
                slot_facts,
                fallback,
            )
            for index in island
        ]
        source_materials = {facts.material_name for facts in island_facts}
        source_texture_paths = {
            path
            for facts in island_facts
            for path in facts.source_texture_paths
        }
        region_obj["source_materials"] = json.dumps(sorted(source_materials))
        region_obj["source_texture_paths"] = json.dumps(
            sorted(source_texture_paths)
        )
        for key in (
            "greenbelt_type",
            "strategy_hint",
            "classification_source",
            "street_tree_confidence",
            "classification_evidence",
        ):
            if key in rule:
                value = rule[key]
                region_obj[key] = (
                    json.dumps(value)
                    if isinstance(value, (list, dict))
                    else value
                )
        regions.append(region_to_dict(region_obj, boundary_loops))
        metrics.append(object_geometry_metrics(region_obj, boundary_loops))
        if stage_callback is not None and (
            (island_index + 1) % island_progress_step == 0
            or island_index + 1 == island_total
        ):
            stage_callback(
                "building_region_geometry",
                0.6 + 0.22 * (island_index + 1) / island_total,
            )
    built_at = time.perf_counter()
    return (
        {
            "schema": "verdflow.regions",
            "version": "1",
            "coordinate_space": "source_scene_world",
            "offset_applied": False,
            "source_name": source_name,
            "region_count": len(regions),
            "regions": regions,
        },
        {
            "schema": "verdflow.region-metrics",
            "version": "1",
            "coordinate_space": "source_scene_world",
            "offset_applied": False,
            "region_count": len(metrics),
            "regions": metrics,
            "skipped_region_count": len(islands_to_build) - len(metrics),
            "skipped_regions_sample": skipped_candidates,
            "performance": {
                "source_load_mode": source_load_mode,
                "source_load_seconds": loaded_at - started,
                "material_scan_seconds": scanned_at - loaded_at,
                "island_find_seconds": islands_found_at - scanned_at,
                "region_build_seconds": built_at - islands_found_at,
                "total_seconds": built_at - started,
                "candidate_region_count": len(islands_to_build),
                "skipped_region_count": len(islands_to_build) - len(metrics),
            },
        },
    )
