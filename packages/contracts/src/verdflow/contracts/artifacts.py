"""Artifact contracts exposed by the VerdFlow Core boundary."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    artifact_id: str
    content_hash: str
    media_type: str
    byte_size: int
    schema_id: str | None = None
    schema_version: str | None = None


@dataclass(frozen=True, slots=True)
class GarbageCollectionResult:
    removed_object_count: int
    removed_byte_size: int
    removed_staging_file_count: int

