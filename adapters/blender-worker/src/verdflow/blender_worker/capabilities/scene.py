"""Blender-backed scene import and objective fact collection."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import bpy
from mathutils import Matrix, Vector

from ..algorithms import logical_texture_path


SOURCE_MEMBER_PROP = "verdflow_source_member_id"
SOURCE_TEXTURE_PATH_PROP = "verdflow_source_relative_path"
SOURCE_TEXTURE_SCOPE_PROP = "verdflow_texture_path_scope"


def import_fbx(
    source_path: Path,
    import_settings: dict[str, object] | None = None,
    *,
    clear_scene: bool = True,
) -> None:
    if clear_scene:
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete()
    result = bpy.ops.import_scene.fbx(
        filepath=str(source_path),
        **dict(import_settings or {}),
    )
    if "FINISHED" not in result:
        raise RuntimeError(f"FBX import failed: {result}")


def import_sources(
    *,
    source_path: Path | None = None,
    source_members: list[dict[str, object]] | None = None,
    import_settings: dict[str, object] | None = None,
) -> None:
    if source_members is None:
        if source_path is None:
            raise ValueError("source_path or source_members is required")
        import_fbx(source_path, import_settings)
        return
    if source_path is not None:
        raise ValueError("source_path and source_members are mutually exclusive")
    if not source_members:
        raise ValueError("source_members must not be empty")

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for member in source_members:
        member_id = str(member.get("source_member_id", "")).strip()
        member_path = Path(str(member.get("source_path", "")))
        matrix_values = member.get("source_to_scene_matrix_row_major")
        if not member_id or not member_path.is_file():
            raise ValueError("Source member identity and path are required")
        if not isinstance(matrix_values, list) or len(matrix_values) != 16:
            raise ValueError("Source member matrix must contain 16 values")
        transform = Matrix(
            tuple(
                tuple(float(matrix_values[row * 4 + column]) for column in range(4))
                for row in range(4)
            )
        )
        before = set(bpy.data.objects)
        import_fbx(member_path, import_settings, clear_scene=False)
        imported = [obj for obj in bpy.data.objects if obj not in before]
        imported_set = set(imported)
        roots = [obj for obj in imported if obj.parent not in imported_set]
        for root in roots:
            root.matrix_world = transform @ root.matrix_world
        for obj in imported:
            obj[SOURCE_MEMBER_PROP] = member_id
        bpy.context.view_layer.update()


def load_scene_inputs(
    *,
    source_path: Path | None = None,
    source_members: list[dict[str, object]] | None = None,
    import_settings: dict[str, object] | None = None,
    scene_cache_path: Path | None = None,
    stage_callback: Any | None = None,
) -> str:
    """Load a disposable derived scene when available, else rebuild from Source."""
    if scene_cache_path is not None and scene_cache_path.is_file():
        if stage_callback is not None:
            stage_callback("loading_scene_cache", 0.1)
        try:
            result = bpy.ops.wm.open_mainfile(filepath=str(scene_cache_path))
            if "FINISHED" not in result:
                raise RuntimeError(f"Scene cache load failed: {result}")
            if stage_callback is not None:
                stage_callback("scene_ready", 0.2)
            return "scene_cache"
        except Exception:
            bpy.ops.wm.read_factory_settings(use_empty=True)
    if stage_callback is not None:
        stage_callback("importing_fbx", 0.1)
    import_sources(
        source_path=source_path,
        source_members=source_members,
        import_settings=import_settings,
    )
    if stage_callback is not None:
        stage_callback("scene_ready", 0.2)
    return "source_import"


def save_scene_cache(path: Path) -> None:
    """Write a disposable Blender scene copy without changing project truth."""
    path.parent.mkdir(parents=True, exist_ok=True)
    result = bpy.ops.wm.save_as_mainfile(
        filepath=str(path),
        check_existing=False,
        compress=True,
        copy=True,
    )
    if "FINISHED" not in result:
        raise RuntimeError(f"Scene cache save failed: {result}")


def object_bbox_world(obj: Any) -> list[float]:
    coordinates = [
        obj.matrix_world @ Vector(corner) for corner in obj.bound_box
    ]
    return [
        min(value.x for value in coordinates),
        min(value.y for value in coordinates),
        min(value.z for value in coordinates),
        max(value.x for value in coordinates),
        max(value.y for value in coordinates),
        max(value.z for value in coordinates),
    ]


def material_texture_infos(
    material: Any,
    source_roots: tuple[Path, ...] = (),
) -> list[dict[str, str]]:
    if material is None:
        return []
    textures: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    if material.use_nodes and material.node_tree:
        for node in material.node_tree.nodes:
            image = getattr(node, "image", None)
            if node.bl_idname != "ShaderNodeTexImage" or image is None:
                continue
            key = (image.name, image.filepath)
            if key in seen:
                continue
            seen.add(key)
            logical_path, path_scope = logical_texture_path(
                bpy.path.abspath(image.filepath) if image.filepath else "",
                source_roots,
            )
            cached_path = image.get(SOURCE_TEXTURE_PATH_PROP)
            cached_scope = image.get(SOURCE_TEXTURE_SCOPE_PROP)
            if cached_path:
                logical_path = str(cached_path)
                path_scope = str(cached_scope or "source_relative")
            elif logical_path:
                image[SOURCE_TEXTURE_PATH_PROP] = logical_path
                image[SOURCE_TEXTURE_SCOPE_PROP] = path_scope
            textures.append(
                {
                    "node": node.name,
                    "image": image.name,
                    "source_relative_path": logical_path,
                    "path_scope": path_scope,
                    "source": "material_node",
                }
            )
    return textures


def face_material(obj: Any, polygon: Any) -> Any:
    slots = list(obj.material_slots)
    if polygon.material_index < len(slots):
        return slots[polygon.material_index].material
    return None


def mesh_material_distribution(obj: Any) -> dict[str, int]:
    distribution: dict[str, int] = {}
    for polygon in obj.data.polygons:
        material = face_material(obj, polygon)
        name = material.name if material else "__NO_MATERIAL__"
        distribution[name] = distribution.get(name, 0) + 1
    return distribution


def collect_fact_report(
    source_roots: tuple[Path, ...] = (),
) -> dict[str, object]:
    material_rows: dict[str, dict[str, Any]] = {}
    object_rows: list[dict[str, object]] = []
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        distribution = mesh_material_distribution(obj)
        object_rows.append(
            {
                "name": obj.name,
                "type": obj.type,
                "source_member_id": obj.get(SOURCE_MEMBER_PROP),
                "face_count": len(obj.data.polygons),
                "vertex_count": len(obj.data.vertices),
                "bbox": object_bbox_world(obj),
                "materials": [
                    {"name": name, "faces": faces}
                    for name, faces in sorted(
                        distribution.items(),
                        key=lambda item: item[1],
                        reverse=True,
                    )
                ],
            }
        )
        materials_by_name = {
            slot.material.name: slot.material
            for slot in obj.material_slots
            if slot.material is not None
        }
        for name, faces in distribution.items():
            material = materials_by_name.get(name)
            row = material_rows.setdefault(
                name,
                {
                    "name": name,
                    "face_count": 0,
                    "objects": set(),
                    "diffuse_color": (
                        [float(value) for value in material.diffuse_color]
                        if material is not None
                        else [0.8, 0.8, 0.8, 1.0]
                    ),
                    "textures": material_texture_infos(material, source_roots),
                },
            )
            row["face_count"] += faces
            if faces:
                row["objects"].add(obj.name)

    materials: list[dict[str, object]] = []
    for row in material_rows.values():
        item = dict(row)
        item["objects"] = sorted(row["objects"])
        item["object_count"] = len(item["objects"])
        materials.append(item)
    materials.sort(key=lambda item: item["face_count"], reverse=True)
    object_rows.sort(key=lambda item: item["face_count"], reverse=True)
    return {
        "schema": "fbx-region-tagger.fact-report.v1",
        "coordinate_space": "blender_world_z_up",
        "mesh_object_count": len(object_rows),
        "material_count": len(materials),
        "materials": materials,
        "objects": object_rows,
    }


def collect_scene_manifest(
    *,
    source_name: str,
    import_settings: dict[str, object],
) -> dict[str, object]:
    scene = bpy.context.scene
    objects: list[dict[str, object]] = []
    for obj in sorted(bpy.data.objects, key=lambda item: item.name):
        row: dict[str, object] = {
            "name": obj.name,
            "type": obj.type,
            "source_member_id": obj.get(SOURCE_MEMBER_PROP),
            "parent": obj.parent.name if obj.parent else None,
            "matrix_world": [
                [float(value) for value in row_values]
                for row_values in obj.matrix_world
            ],
        }
        if obj.type == "MESH":
            row.update(
                {
                    "face_count": len(obj.data.polygons),
                    "vertex_count": len(obj.data.vertices),
                    "world_bounds": object_bbox_world(obj),
                    "material_slots": [
                        slot.material.name if slot.material else None
                        for slot in obj.material_slots
                    ],
                }
            )
        objects.append(row)
    build_hash = bpy.app.build_hash
    if isinstance(build_hash, bytes):
        build_hash = build_hash.decode("utf-8", errors="replace")
    return {
        "schema": "verdflow.scene-inspection-manifest",
        "version": "1",
        "source_name": source_name,
        "producer": {
            "id": "verdflow-blender-worker",
            "blender_version": bpy.app.version_string,
            "blender_build_hash": str(build_hash),
        },
        "import_settings": import_settings,
        "scene_units": {
            "system": scene.unit_settings.system,
            "scale_length": scene.unit_settings.scale_length,
            "length_unit": scene.unit_settings.length_unit,
        },
        "coordinate_frame": {
            "name": "Blender world",
            "handedness": "right-handed",
            "up_axis": "+Z",
            "linear_unit": "meter",
            "crs_kind": "unknown",
            "crs_definition": None,
            "local_origin": [0.0, 0.0, 0.0],
            "offset_applied": False,
        },
        "object_count": len(objects),
        "objects": objects,
    }


def inspect_scene(
    source_path: Path | None,
    *,
    source_name: str,
    source_members: list[dict[str, object]] | None = None,
    import_settings: dict[str, object] | None = None,
    stage_callback: Any | None = None,
    cancel_check: Any | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
    settings = dict(import_settings or {})
    load_scene_inputs(
        source_path=source_path,
        source_members=source_members,
        import_settings=settings,
        stage_callback=stage_callback,
    )
    if cancel_check is not None:
        cancel_check()
    roots = source_roots_for_inputs(source_path, source_members)
    if stage_callback is not None:
        stage_callback("collecting_scene_manifest", 0.35)
    manifest = collect_scene_manifest(
        source_name=source_name,
        import_settings=settings,
    )
    if cancel_check is not None:
        cancel_check()
    if stage_callback is not None:
        stage_callback("collecting_material_facts", 0.55)
    fact_report = collect_fact_report(roots)
    return (
        manifest,
        fact_report,
    )


def source_roots_for_inputs(
    source_path: Path | None,
    source_members: list[dict[str, object]] | None,
) -> tuple[Path, ...]:
    if source_members is not None:
        return tuple(
            Path(str(member["source_path"])).parent
            for member in source_members
        )
    return (source_path.parent,) if source_path is not None else ()
