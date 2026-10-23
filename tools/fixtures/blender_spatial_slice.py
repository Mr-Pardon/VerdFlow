"""Build a coordinate-preserving FBX slice inside Blender."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import bpy
import numpy as np


TOOL_VERSION = "1.0.0"


def main() -> None:
    args = _parse_args()
    source_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()
    report_path = Path(args.report).resolve()
    bounds = (args.min_x, args.min_y, args.max_x, args.max_y)
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    if args.min_x >= args.max_x or args.min_y >= args.max_y:
        raise ValueError("Slice bounds must have positive width and height")

    started = time.perf_counter()
    _clear_scene()
    imported = bpy.ops.import_scene.fbx(filepath=str(source_path))
    if "FINISHED" not in imported:
        raise RuntimeError(f"FBX import failed: {imported}")
    imported_at = time.perf_counter()

    source_objects = [
        obj for obj in bpy.context.scene.objects if obj.type == "MESH"
    ]
    slices: list[Any] = []
    object_reports: list[dict[str, Any]] = []
    for source_object in source_objects:
        sliced_object, object_report = _slice_object(source_object, bounds)
        object_reports.append(object_report)
        if sliced_object is not None:
            slices.append(sliced_object)

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for sliced_object in slices:
        bpy.context.scene.collection.objects.link(sliced_object)
    bpy.context.view_layer.update()
    sliced_at = time.perf_counter()

    if not slices:
        raise RuntimeError("Slice window did not select any mesh faces")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    exported = bpy.ops.export_scene.fbx(
        filepath=str(output_path),
        use_selection=False,
        axis_forward="-Y",
        axis_up="Z",
        apply_unit_scale=True,
        path_mode="STRIP",
        embed_textures=False,
        use_custom_props=True,
    )
    if "FINISHED" not in exported:
        raise RuntimeError(f"FBX export failed: {exported}")
    exported_at = time.perf_counter()

    selected_objects = [row for row in object_reports if row["selected_faces"]]
    report = {
        "schema": "verdflow.spatial-fbx-slice-report",
        "version": "1",
        "builder_version": TOOL_VERSION,
        "source_path": str(source_path),
        "output_path": str(output_path),
        "coordinate_space": "source_scene_world",
        "offset_applied": False,
        "geometry_transform": "object_world_matrix_baked_into_vertices",
        "selection_rule": "polygon_center_inside_xy_bounds",
        "bounds_xy": list(bounds),
        "source_mesh_object_count": len(source_objects),
        "selected_mesh_object_count": len(selected_objects),
        "source_vertex_count": sum(row["source_vertices"] for row in object_reports),
        "source_face_count": sum(row["source_faces"] for row in object_reports),
        "selected_vertex_count": sum(
            row["selected_vertices"] for row in object_reports
        ),
        "selected_face_count": sum(row["selected_faces"] for row in object_reports),
        "selected_material_count": len(
            {
                material
                for row in selected_objects
                for material in row["selected_materials"]
            }
        ),
        "objects": object_reports,
        "performance": {
            "import_seconds": imported_at - started,
            "slice_seconds": sliced_at - imported_at,
            "export_seconds": exported_at - sliced_at,
            "total_seconds": exported_at - started,
        },
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--min-x", type=float, required=True)
    parser.add_argument("--min-y", type=float, required=True)
    parser.add_argument("--max-x", type=float, required=True)
    parser.add_argument("--max-y", type=float, required=True)
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    return parser.parse_args(argv)


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _slice_object(
    source_object: Any,
    bounds: tuple[float, float, float, float],
) -> tuple[Any | None, dict[str, Any]]:
    mesh = source_object.data
    source_vertex_count = len(mesh.vertices)
    source_face_count = len(mesh.polygons)
    if not source_vertex_count or not source_face_count:
        return None, _object_report(source_object, 0, 0, [], None)

    centers = np.empty((source_face_count, 3), dtype=np.float64)
    mesh.polygons.foreach_get("center", centers.reshape(-1))
    matrix = np.asarray(source_object.matrix_world, dtype=np.float64)
    world_centers = centers @ matrix[:3, :3].T + matrix[:3, 3]
    min_x, min_y, max_x, max_y = bounds
    selected_indices = np.flatnonzero(
        (world_centers[:, 0] >= min_x)
        & (world_centers[:, 0] <= max_x)
        & (world_centers[:, 1] >= min_y)
        & (world_centers[:, 1] <= max_y)
    )
    if not selected_indices.size:
        return None, _object_report(source_object, 0, 0, [], None)

    coordinates = np.empty((source_vertex_count, 3), dtype=np.float64)
    mesh.vertices.foreach_get("co", coordinates.reshape(-1))
    world_coordinates = coordinates @ matrix[:3, :3].T + matrix[:3, 3]
    loop_vertex_indices = np.empty(len(mesh.loops), dtype=np.int64)
    mesh.loops.foreach_get("vertex_index", loop_vertex_indices)

    vertex_map: dict[int, int] = {}
    vertices: list[list[float]] = []
    faces: list[tuple[int, ...]] = []
    source_material_indices: list[int] = []
    for polygon_index in selected_indices:
        polygon = mesh.polygons[int(polygon_index)]
        face: list[int] = []
        for loop_index in range(
            polygon.loop_start,
            polygon.loop_start + polygon.loop_total,
        ):
            source_vertex_index = int(loop_vertex_indices[loop_index])
            target_vertex_index = vertex_map.get(source_vertex_index)
            if target_vertex_index is None:
                target_vertex_index = len(vertices)
                vertex_map[source_vertex_index] = target_vertex_index
                vertices.append(world_coordinates[source_vertex_index].tolist())
            face.append(target_vertex_index)
        faces.append(tuple(face))
        source_material_indices.append(int(polygon.material_index))

    sliced_mesh = bpy.data.meshes.new(source_object.data.name)
    sliced_mesh.from_pydata(vertices, [], faces)
    material_index_map = _copy_used_materials(
        mesh,
        sliced_mesh,
        source_material_indices,
    )
    for polygon, source_material_index in zip(
        sliced_mesh.polygons,
        source_material_indices,
        strict=True,
    ):
        polygon.material_index = material_index_map[source_material_index]
    sliced_mesh.update()
    sliced_object = bpy.data.objects.new(source_object.name, sliced_mesh)
    for key in source_object.keys():
        sliced_object[key] = source_object[key]

    used_material_names = [
        sliced_mesh.materials[index].name
        for index in range(len(sliced_mesh.materials))
        if sliced_mesh.materials[index] is not None
    ]
    selected_world = np.asarray(vertices, dtype=np.float64)
    selected_bounds = [
        float(selected_world[:, 0].min()),
        float(selected_world[:, 1].min()),
        float(selected_world[:, 2].min()),
        float(selected_world[:, 0].max()),
        float(selected_world[:, 1].max()),
        float(selected_world[:, 2].max()),
    ]
    return sliced_object, _object_report(
        source_object,
        len(vertices),
        len(faces),
        used_material_names,
        selected_bounds,
    )


def _copy_used_materials(
    source_mesh: Any,
    target_mesh: Any,
    source_material_indices: list[int],
) -> dict[int, int]:
    mapping: dict[int, int] = {}
    for source_index in sorted(set(source_material_indices)):
        if source_index < len(source_mesh.materials):
            material = source_mesh.materials[source_index]
        else:
            material = None
        if material is None:
            material = bpy.data.materials.get("VerdFlow_Unassigned")
            if material is None:
                material = bpy.data.materials.new("VerdFlow_Unassigned")
        mapping[source_index] = len(target_mesh.materials)
        target_mesh.materials.append(material)
    return mapping


def _object_report(
    source_object: Any,
    selected_vertex_count: int,
    selected_face_count: int,
    selected_materials: list[str],
    selected_bounds: list[float] | None,
) -> dict[str, Any]:
    return {
        "object_name": source_object.name,
        "mesh_name": source_object.data.name,
        "source_vertices": len(source_object.data.vertices),
        "source_faces": len(source_object.data.polygons),
        "selected_vertices": selected_vertex_count,
        "selected_faces": selected_face_count,
        "selected_materials": selected_materials,
        "selected_bounds_xyz": selected_bounds,
    }


if __name__ == "__main__":
    main()
