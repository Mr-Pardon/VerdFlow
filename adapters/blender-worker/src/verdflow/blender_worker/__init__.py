"""Public entry points for the VerdFlow Blender worker adapter."""

from __future__ import annotations

from importlib import import_module
from typing import Any


__all__ = [
    "BLENDER_EXTRACT_REGIONS_COMMAND",
    "BLENDER_INSPECT_ASSET_LIBRARY_COMMAND",
    "BLENDER_INSPECT_SCENE_COMMAND",
    "BLENDER_PROBE_COMMAND",
    "BLENDER_SAMPLE_ELEVATION_COMMAND",
    "BLENDER_SUPPORTED_COMMANDS",
    "BLENDER_WORKER_ADAPTER_ID",
    "BlenderWorkerConfig",
    "BlenderWorkerError",
    "BlenderWorkerSupervisor",
    "BlenderAssetLibraryRegistration",
    "BlenderAssetLibraryRegistry",
    "build_blender_asset_catalog",
    "locate_blender",
]

_EXPORT_MODULES = {
    "BLENDER_EXTRACT_REGIONS_COMMAND": ".protocol",
    "BLENDER_INSPECT_ASSET_LIBRARY_COMMAND": ".protocol",
    "BLENDER_INSPECT_SCENE_COMMAND": ".protocol",
    "BLENDER_PROBE_COMMAND": ".protocol",
    "BLENDER_SAMPLE_ELEVATION_COMMAND": ".protocol",
    "BLENDER_SUPPORTED_COMMANDS": ".protocol",
    "BLENDER_WORKER_ADAPTER_ID": ".protocol",
    "BlenderWorkerConfig": ".protocol",
    "BlenderWorkerError": ".protocol",
    "BlenderWorkerSupervisor": ".supervisor",
    "BlenderAssetLibraryRegistration": ".asset_libraries",
    "BlenderAssetLibraryRegistry": ".asset_libraries",
    "build_blender_asset_catalog": ".catalogs",
    "locate_blender": ".discovery",
}


def __getattr__(name: str) -> Any:
    try:
        module_name = _EXPORT_MODULES[name]
    except KeyError as error:
        raise AttributeError(name) from error
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value
