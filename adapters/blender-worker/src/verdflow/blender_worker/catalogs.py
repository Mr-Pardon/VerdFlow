"""Map inspected Blender Collection assets to a target catalog document."""

from __future__ import annotations

import copy
from typing import Any

from .hashing import CONTENT_HASH
from .protocol import BlenderWorkerError


INSPECTION_SCHEMA = "verdflow.blender-asset-library-inspection"


def build_blender_asset_catalog(
    inspection: dict[str, Any],
    *,
    catalog_id: str,
    display_name: str,
    provider_id: str = "blender-local-library",
) -> dict[str, Any]:
    """Build a Planting-compatible Catalog without retaining local paths."""

    if inspection.get("schema") != INSPECTION_SCHEMA or str(inspection.get("version")) != "1":
        raise BlenderWorkerError("Blender asset library inspection version is unsupported")
    library_id = _required_text(inspection, "library_id")
    content_hash = _required_text(inspection, "library_content_hash")
    if not CONTENT_HASH.fullmatch(content_hash):
        raise BlenderWorkerError(
            "library_content_hash must use sha256:<64 lowercase hex>"
        )
    raw_assets = inspection.get("assets")
    if not isinstance(raw_assets, list) or not raw_assets:
        raise BlenderWorkerError("Blender asset library contains no Collection assets")
    assets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_assets:
        if not isinstance(raw, dict):
            raise BlenderWorkerError("Blender asset inspection record must be an object")
        if raw.get("datablock_type") != "COLLECTION":
            raise BlenderWorkerError(
                "Blender asset catalog v1 only accepts Collection assets"
            )
        name = _required_text(raw, "datablock_name")
        if name in seen:
            raise BlenderWorkerError(f"Blender Collection asset is duplicated: {name}")
        seen.add(name)
        assets.append(
            {
                "asset_id": name,
                "display_name": _required_text(raw, "display_name"),
                "provider_metadata": {
                    "library_id": library_id,
                    "library_content_hash": content_hash,
                    "datablock_type": "COLLECTION",
                    "datablock_name": name,
                    "instance_mode": "collection_instance",
                    "inspection_facts": {
                        key: copy.deepcopy(raw[key])
                        for key in (
                            "object_count",
                            "mesh_object_count",
                        )
                        if key in raw
                    },
                },
            }
        )
    return {
        "schema": "verdflow.target-asset-catalog",
        "version": "1",
        "catalog_id": catalog_id,
        "target_type": "blender",
        "provider_id": provider_id,
        "display_name": display_name,
        "assets": sorted(assets, key=lambda item: item["asset_id"]),
    }


def _required_text(value: dict[str, Any], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item.strip():
        raise BlenderWorkerError(f"{field} is required")
    return item.strip()
