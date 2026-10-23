"""Canonical scene provenance shared across VerdFlow boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SourceKind(StrEnum):
    ASSEMBLY = "assembly"
    FBX = "fbx"
    BLEND = "blend"
    GLTF = "gltf"
    SHAPEFILE = "shapefile"
    GEOJSON = "geojson"
    GEOTIFF = "geotiff"
    XML = "xml"
    OTHER = "other"


class CoordinateReferenceKind(StrEnum):
    LOCAL = "local"
    EPSG = "epsg"
    WKT = "wkt"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class CoordinateFrameInfo:
    coordinate_frame_id: str
    name: str
    handedness: str
    up_axis: str
    linear_unit: str
    crs_kind: CoordinateReferenceKind
    crs_definition: str | None
    local_origin_x: float
    local_origin_y: float
    local_origin_z: float
    created_at: str


@dataclass(frozen=True, slots=True)
class SourceInfo:
    source_id: str
    kind: SourceKind
    original_name: str
    artifact_id: str
    content_hash: str
    imported_at: str


@dataclass(frozen=True, slots=True)
class SourceAssemblyMemberSpec:
    source_id: str
    source_to_scene_matrix_row_major: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class SourceAssemblyMemberInfo:
    source_member_id: str
    assembly_source_id: str
    source_id: str
    ordinal: int
    source_content_hash: str
    source_to_scene_matrix_row_major: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class SceneRevisionInfo:
    scene_revision_id: str
    source_id: str
    parent_revision_id: str | None
    importer_id: str
    importer_version: str
    coordinate_frame_id: str
    scene_manifest_artifact_id: str
    created_at: str
