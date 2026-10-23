"""Portable asset package values with no persistence dependency."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib

from verdflow.contracts import (
    AssetCatalogDocument,
    require_library_resource_id,
    require_sha256_hash,
)


@dataclass(frozen=True, slots=True)
class AssetPackageResourceMetadata:
    resource_id: str
    content_hash: str
    media_type: str
    byte_size: int
    path: str

    def __post_init__(self) -> None:
        require_library_resource_id(self.resource_id)
        require_sha256_hash(self.content_hash)
        if self.content_hash.removeprefix("sha256:") != self.resource_id.removeprefix(
            "res_"
        ):
            raise ValueError("resource_id does not match content_hash")
        if not isinstance(self.media_type, str) or not self.media_type.strip():
            raise ValueError("media_type is required")
        if (
            not isinstance(self.byte_size, int)
            or isinstance(self.byte_size, bool)
            or self.byte_size <= 0
        ):
            raise ValueError("byte_size must be positive")
        if not isinstance(self.path, str) or not self.path:
            raise ValueError("path is required")


@dataclass(frozen=True, slots=True)
class AssetPackageResource:
    resource_id: str
    content_hash: str
    media_type: str
    payload: bytes

    def __post_init__(self) -> None:
        require_library_resource_id(self.resource_id)
        require_sha256_hash(self.content_hash)
        if not isinstance(self.media_type, str) or not self.media_type.strip():
            raise ValueError("media_type is required")
        if not isinstance(self.payload, bytes) or not self.payload:
            raise ValueError("payload must be non-empty bytes")
        actual = f"sha256:{hashlib.sha256(self.payload).hexdigest()}"
        if actual != self.content_hash:
            raise ValueError("content_hash does not match payload")
        if actual.removeprefix("sha256:") != self.resource_id.removeprefix("res_"):
            raise ValueError("resource_id does not match payload")


@dataclass(frozen=True, slots=True)
class AssetPackageInspection:
    """An immutable inspection of one exported current Catalog snapshot."""

    source_path: str
    package_hash: str
    catalog: AssetCatalogDocument
    resources: tuple[AssetPackageResourceMetadata, ...]


@dataclass(frozen=True, slots=True)
class AssetPackageArchiveInfo:
    destination_path: str
    package_hash: str
    byte_size: int
    resource_count: int
