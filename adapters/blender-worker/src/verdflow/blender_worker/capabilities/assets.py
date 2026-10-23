"""Inspect Collection assets from one registered Blender library."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import bpy

from verdflow.blender_worker.hashing import file_content_hash


def inspect_asset_library(
    library_path: Path,
    *,
    library_id: str,
    required_collection_names: list[str] | None = None,
) -> dict[str, Any]:
    """Return objective facts for marked Collection assets only."""

    required = set(required_collection_names or [])
    before = _file_identity(library_path)
    with bpy.data.libraries.load(str(library_path), link=False) as (source, target):
        target.collections = list(source.collections)
    after_load = _file_identity(library_path)
    if after_load != before:
        raise ValueError("Blender asset library changed while it was being loaded")
    library_content_hash = file_content_hash(library_path)
    if _file_identity(library_path) != before:
        raise ValueError("Blender asset library changed while it was being inspected")

    assets: list[dict[str, Any]] = []
    for collection in sorted(
        (item for item in target.collections if item is not None),
        key=lambda item: item.name,
    ):
        if collection.asset_data is None:
            continue
        objects = list(collection.all_objects)
        assets.append(
            {
                "datablock_type": "COLLECTION",
                "datablock_name": collection.name,
                "display_name": collection.name,
                "object_count": len(objects),
                "mesh_object_count": sum(1 for item in objects if item.type == "MESH"),
            }
        )

    names = {item["datablock_name"] for item in assets}
    missing = sorted(required - names)
    if missing:
        raise ValueError(
            "Required Blender Collection assets are missing: " + ", ".join(missing)
        )
    return {
        "schema": "verdflow.blender-asset-library-inspection",
        "version": "1",
        "library_id": library_id,
        "library_content_hash": library_content_hash,
        "provider_observation": {
            "access_mode": "direct_observed",
            "byte_size": before[0],
            "modified_time_ns": before[1],
        },
        "asset_type": "COLLECTION",
        "asset_count": len(assets),
        "assets": assets,
    }


def _file_identity(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns
