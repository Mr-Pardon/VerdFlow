"""Headless Blender capabilities exposed to the worker entry."""

from .assets import inspect_asset_library
from .elevation import sample_elevation
from .manifest import capability_manifest
from .regions import extract_regions
from .scene import inspect_scene, save_scene_cache

__all__ = [
    "capability_manifest",
    "extract_regions",
    "inspect_asset_library",
    "inspect_scene",
    "save_scene_cache",
    "sample_elevation",
]
