"""Deterministic and bounded archive IO for current Catalog snapshots."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable
import uuid
import zipfile

from verdflow.contracts import (
    AssetCatalogDocument,
    parse_library_resource_reference,
    require_library_resource_id,
    require_sha256_hash,
)

from .errors import AssetPackageError
from .models import (
    AssetPackageArchiveInfo,
    AssetPackageInspection,
    AssetPackageResource,
    AssetPackageResourceMetadata,
)


PACKAGE_SCHEMA = "verdflow.asset-catalog-package"
PACKAGE_VERSION = "1"
MANIFEST_PATH = "manifest.json"
CATALOG_PATH = "catalog.json"
MAX_PACKAGE_BYTES = 512 * 1024 * 1024
MAX_CATALOG_BYTES = 16 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_RESOURCE_BYTES = 16 * 1024 * 1024
MAX_RESOURCE_COUNT = 4096
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def catalog_resource_ids(document: AssetCatalogDocument) -> tuple[str, ...]:
    return _catalog_resource_ids(_json_object(document.canonical_json, "Catalog"))


def write_asset_catalog_package(
    document: AssetCatalogDocument,
    resources: Iterable[AssetPackageResource],
    destination: Path,
) -> AssetPackageArchiveInfo:
    if len(document.canonical_json) > MAX_CATALOG_BYTES:
        raise AssetPackageError("Catalog exceeds the 16 MiB limit")
    expected_resource_ids = set(catalog_resource_ids(document))
    destination_path = destination.expanduser()
    parent = destination_path.parent.resolve(strict=True)
    destination_path = parent / destination_path.name
    if destination_path.exists() and destination_path.is_dir():
        raise ValueError(f"Asset package destination is a directory: {destination_path}")
    staged = parent / f".{destination_path.name}.{uuid.uuid4().hex}.tmp"
    metadata: list[AssetPackageResourceMetadata] = []
    seen: set[str] = set()
    previous_id: str | None = None
    expanded_size = len(document.canonical_json)
    try:
        with zipfile.ZipFile(
            staged,
            mode="x",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as archive:
            _write_entry(archive, CATALOG_PATH, document.canonical_json)
            for resource in resources:
                if len(metadata) >= MAX_RESOURCE_COUNT:
                    raise AssetPackageError("Asset package contains too many resources")
                if len(resource.payload) > MAX_RESOURCE_BYTES:
                    raise AssetPackageError(
                        f"Resource exceeds the 16 MiB limit: {resource.resource_id}"
                    )
                if resource.resource_id in seen:
                    raise AssetPackageError(
                        f"Duplicate resource supplied: {resource.resource_id}"
                    )
                if previous_id is not None and resource.resource_id <= previous_id:
                    raise AssetPackageError(
                        "Resources must be supplied in ascending resource_id order"
                    )
                path = _resource_path(resource.resource_id)
                item = AssetPackageResourceMetadata(
                    resource_id=resource.resource_id,
                    content_hash=resource.content_hash,
                    media_type=resource.media_type,
                    byte_size=len(resource.payload),
                    path=path,
                )
                _write_entry(archive, path, resource.payload)
                expanded_size += len(resource.payload)
                if expanded_size > MAX_PACKAGE_BYTES:
                    raise AssetPackageError(
                        "Asset package expands beyond the 512 MiB limit"
                    )
                metadata.append(item)
                seen.add(resource.resource_id)
                previous_id = resource.resource_id
            if seen != expected_resource_ids:
                raise AssetPackageError(
                    "Catalog resource references do not match supplied resources"
                )
            manifest_bytes = _canonical_json_bytes(_manifest(document, metadata))
            if len(manifest_bytes) > MAX_MANIFEST_BYTES:
                raise AssetPackageError("Asset package manifest exceeds the 1 MiB limit")
            if expanded_size + len(manifest_bytes) > MAX_PACKAGE_BYTES:
                raise AssetPackageError(
                    "Asset package expands beyond the 512 MiB limit"
                )
            _write_entry(archive, MANIFEST_PATH, manifest_bytes)
        if staged.stat().st_size > MAX_PACKAGE_BYTES:
            raise AssetPackageError("Asset package exceeds the 512 MiB limit")
        with staged.open("r+b") as stream:
            os.fsync(stream.fileno())
        os.replace(staged, destination_path)
    finally:
        staged.unlink(missing_ok=True)

    return AssetPackageArchiveInfo(
        destination_path=str(destination_path),
        package_hash=_file_hash(destination_path),
        byte_size=destination_path.stat().st_size,
        resource_count=len(metadata),
    )


def inspect_asset_catalog_package(source: Path) -> AssetPackageInspection:
    source_path, package_hash = _validated_source(source)
    try:
        with zipfile.ZipFile(source_path, mode="r") as archive:
            return _inspection_from_archive(
                archive,
                source_path=source_path,
                package_hash=package_hash,
            )
    except zipfile.BadZipFile as error:
        raise AssetPackageError("Asset package is not a valid ZIP archive") from error


def read_asset_catalog_package(
    source: Path,
    *,
    expected_package_hash: str | None = None,
    on_resource: Callable[[AssetPackageResource], None] | None = None,
) -> AssetPackageInspection:
    inspection = inspect_asset_catalog_package(source)
    if expected_package_hash is not None and inspection.package_hash != expected_package_hash:
        raise AssetPackageError("Asset package changed after inspection")
    try:
        with zipfile.ZipFile(inspection.source_path, mode="r") as archive:
            members = _validated_members(archive)
            for metadata in inspection.resources:
                payload = _read_member(
                    archive,
                    members[metadata.path],
                    MAX_RESOURCE_BYTES,
                )
                try:
                    resource = AssetPackageResource(
                        resource_id=metadata.resource_id,
                        content_hash=metadata.content_hash,
                        media_type=metadata.media_type,
                        payload=payload,
                    )
                except ValueError as error:
                    raise AssetPackageError(
                        f"Resource content does not match metadata: {metadata.resource_id}"
                    ) from error
                if len(payload) != metadata.byte_size:
                    raise AssetPackageError(
                        f"Resource byte size mismatch: {metadata.resource_id}"
                    )
                if on_resource is not None:
                    on_resource(resource)
    except zipfile.BadZipFile as error:
        raise AssetPackageError("Asset package is not a valid ZIP archive") from error
    if _file_hash(Path(inspection.source_path)) != inspection.package_hash:
        raise AssetPackageError("Asset package changed while it was being read")
    return inspection


def _inspection_from_archive(
    archive: zipfile.ZipFile,
    *,
    source_path: Path,
    package_hash: str,
) -> AssetPackageInspection:
    members = _validated_members(archive)
    manifest_bytes = _read_member(
        archive,
        members[MANIFEST_PATH],
        MAX_MANIFEST_BYTES,
    )
    manifest = _json_object(manifest_bytes, "Manifest")
    if _canonical_json_bytes(manifest) != manifest_bytes:
        raise AssetPackageError("Asset package manifest is not canonical JSON")
    catalog_meta, resources = _validate_manifest(manifest)
    expected_names = {
        MANIFEST_PATH,
        CATALOG_PATH,
        *(item.path for item in resources),
    }
    if set(members) != expected_names:
        missing = sorted(expected_names - set(members))
        unexpected = sorted(set(members) - expected_names)
        raise AssetPackageError(
            "Asset package entries do not match the manifest"
            f"; missing={missing}; unexpected={unexpected}"
        )
    catalog_bytes = _read_member(
        archive,
        members[CATALOG_PATH],
        MAX_CATALOG_BYTES,
    )
    catalog_value = _json_object(catalog_bytes, "Catalog")
    if _canonical_json_bytes(catalog_value) != catalog_bytes:
        raise AssetPackageError("Asset package Catalog is not canonical JSON")
    try:
        document = AssetCatalogDocument(
            target_type=str(catalog_meta["target_type"]),
            provider_id=str(catalog_meta["provider_id"]),
            catalog_id=str(catalog_meta["catalog_id"]),
            display_name=str(
                catalog_value.get("display_name") or catalog_meta["catalog_id"]
            ),
            semantic_hash=str(catalog_meta["semantic_hash"]),
            canonical_json=catalog_bytes,
        )
    except ValueError as error:
        raise AssetPackageError("Asset package Catalog metadata is invalid") from error
    _require_catalog_identity(document, catalog_value)
    if set(catalog_resource_ids(document)) != {item.resource_id for item in resources}:
        raise AssetPackageError(
            "Catalog resource references do not match the manifest"
        )
    return AssetPackageInspection(
        source_path=str(source_path),
        package_hash=package_hash,
        catalog=document,
        resources=resources,
    )


def _manifest(
    document: AssetCatalogDocument,
    resources: list[AssetPackageResourceMetadata],
) -> dict[str, object]:
    return {
        "schema": PACKAGE_SCHEMA,
        "version": PACKAGE_VERSION,
        "catalog": {
            "target_type": document.target_type,
            "provider_id": document.provider_id,
            "catalog_id": document.catalog_id,
            "semantic_hash": document.semantic_hash,
            "path": CATALOG_PATH,
        },
        "resources": [
            {
                "resource_id": item.resource_id,
                "content_hash": item.content_hash,
                "media_type": item.media_type,
                "byte_size": item.byte_size,
                "path": item.path,
            }
            for item in resources
        ],
    }


def _validated_source(source: Path) -> tuple[Path, str]:
    source_path = source.expanduser().resolve(strict=True)
    if not source_path.is_file():
        raise ValueError(f"Asset package is not a file: {source_path}")
    if source_path.stat().st_size > MAX_PACKAGE_BYTES:
        raise AssetPackageError("Asset package exceeds the 512 MiB limit")
    return source_path, _file_hash(source_path)


def _validated_members(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    infos = archive.infolist()
    if len(infos) > MAX_RESOURCE_COUNT + 2:
        raise AssetPackageError("Asset package contains too many entries")
    result: dict[str, zipfile.ZipInfo] = {}
    total_size = 0
    for info in infos:
        name = info.filename
        path = PurePosixPath(name)
        if (
            info.is_dir()
            or not name
            or path.is_absolute()
            or ".." in path.parts
            or "\\" in name
        ):
            raise AssetPackageError(f"Unsafe asset package entry: {name!r}")
        if name in result:
            raise AssetPackageError(f"Duplicate asset package entry: {name}")
        if info.flag_bits & 0x1:
            raise AssetPackageError("Encrypted asset package entries are not supported")
        total_size += info.file_size
        if total_size > MAX_PACKAGE_BYTES:
            raise AssetPackageError("Asset package expands beyond the 512 MiB limit")
        result[name] = info
    if MANIFEST_PATH not in result or CATALOG_PATH not in result:
        raise AssetPackageError("Asset package is missing manifest.json or catalog.json")
    return result


def _validate_manifest(
    manifest: dict[str, object],
) -> tuple[dict[str, object], tuple[AssetPackageResourceMetadata, ...]]:
    if set(manifest) != {"schema", "version", "catalog", "resources"}:
        raise AssetPackageError("Asset package manifest fields are invalid")
    if manifest.get("schema") != PACKAGE_SCHEMA or manifest.get("version") != PACKAGE_VERSION:
        raise AssetPackageError("Unsupported asset package schema or version")
    catalog = manifest.get("catalog")
    resources = manifest.get("resources")
    if not isinstance(catalog, dict) or not isinstance(resources, list):
        raise AssetPackageError("Asset package manifest is incomplete")
    required_catalog = {
        "target_type",
        "provider_id",
        "catalog_id",
        "semantic_hash",
        "path",
    }
    if set(catalog) != required_catalog or catalog.get("path") != CATALOG_PATH:
        raise AssetPackageError("Asset package Catalog metadata is invalid")
    for field in required_catalog - {"semantic_hash", "path"}:
        if not isinstance(catalog.get(field), str) or not str(catalog[field]).strip():
            raise AssetPackageError(f"Catalog metadata {field} is invalid")
    try:
        require_sha256_hash(str(catalog["semantic_hash"]))
    except ValueError as error:
        raise AssetPackageError("Catalog semantic hash is invalid") from error
    if len(resources) > MAX_RESOURCE_COUNT:
        raise AssetPackageError("Asset package declares too many resources")

    validated: list[AssetPackageResourceMetadata] = []
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    required_resource = {
        "resource_id",
        "content_hash",
        "media_type",
        "byte_size",
        "path",
    }
    for raw in resources:
        if not isinstance(raw, dict) or set(raw) != required_resource:
            raise AssetPackageError("Asset package resource metadata is invalid")
        if (
            not isinstance(raw["resource_id"], str)
            or not isinstance(raw["content_hash"], str)
            or not isinstance(raw["media_type"], str)
            or not isinstance(raw["path"], str)
            or not isinstance(raw["byte_size"], int)
            or isinstance(raw["byte_size"], bool)
        ):
            raise AssetPackageError("Asset package resource metadata is invalid")
        try:
            resource_id = raw["resource_id"]
            item = AssetPackageResourceMetadata(
                resource_id=resource_id,
                content_hash=raw["content_hash"],
                media_type=raw["media_type"],
                byte_size=raw["byte_size"],
                path=raw["path"],
            )
        except (KeyError, TypeError, ValueError) as error:
            raise AssetPackageError("Asset package resource metadata is invalid") from error
        if item.byte_size > MAX_RESOURCE_BYTES:
            raise AssetPackageError(f"Resource byte size is invalid: {item.resource_id}")
        if item.path != _resource_path(item.resource_id):
            raise AssetPackageError(f"Resource path is invalid: {item.resource_id}")
        if item.resource_id in seen_ids or item.path in seen_paths:
            raise AssetPackageError(f"Duplicate resource declaration: {item.resource_id}")
        seen_ids.add(item.resource_id)
        seen_paths.add(item.path)
        validated.append(item)
    if [item.resource_id for item in validated] != sorted(seen_ids):
        raise AssetPackageError("Manifest resources are not in deterministic order")
    return dict(catalog), tuple(validated)


def _require_catalog_identity(
    document: AssetCatalogDocument,
    catalog: dict[str, object],
) -> None:
    actual = {
        "target_type": catalog.get("target_type"),
        "provider_id": catalog.get("provider_id"),
        "catalog_id": catalog.get("catalog_id"),
    }
    expected = {
        "target_type": document.target_type,
        "provider_id": document.provider_id,
        "catalog_id": document.catalog_id,
    }
    if actual != expected:
        raise AssetPackageError("Catalog document does not match package metadata")


def _catalog_resource_ids(catalog: dict[str, object]) -> tuple[str, ...]:
    assets = catalog.get("assets")
    if not isinstance(assets, list):
        raise AssetPackageError("Catalog assets are invalid")
    resource_ids: set[str] = set()
    for asset in assets:
        if not isinstance(asset, dict):
            raise AssetPackageError("Catalog asset is invalid")
        thumbnail = asset.get("thumbnail")
        if thumbnail is None:
            continue
        if not isinstance(thumbnail, str):
            raise AssetPackageError("Catalog thumbnail reference is invalid")
        try:
            resource_ids.add(parse_library_resource_reference(thumbnail))
        except ValueError as error:
            raise AssetPackageError(str(error)) from error
    return tuple(sorted(resource_ids))


def _read_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    limit: int,
) -> bytes:
    if info.file_size <= 0 or info.file_size > limit:
        raise AssetPackageError(f"Asset package entry has invalid size: {info.filename}")
    with archive.open(info, mode="r") as stream:
        payload = stream.read(limit + 1)
    if len(payload) > limit or len(payload) != info.file_size:
        raise AssetPackageError(f"Asset package entry size mismatch: {info.filename}")
    return payload


def _write_entry(archive: zipfile.ZipFile, name: str, payload: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o600 << 16
    archive.writestr(info, payload, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def _resource_path(resource_id: str) -> str:
    require_library_resource_id(resource_id)
    return f"resources/{resource_id}"


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _json_object(payload: bytes, label: str) -> dict[str, object]:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AssetPackageError(f"{label} is not valid UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise AssetPackageError(f"{label} root must be an object")
    return value


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"
