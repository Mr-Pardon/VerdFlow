"""Deterministic and bounded archive IO for self-contained Profile exchange."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import uuid
import zipfile

from verdflow.contracts import RoleBindingProfileDocument

from .errors import ProfilePackageError
from .models import (
    ProfilePackageArchiveInfo,
    ProfilePackageInspection,
    ProfileStrategyDependency,
)


PACKAGE_SCHEMA = "verdflow.role-binding-profile-package"
PACKAGE_VERSION = "2"
MANIFEST_PATH = "manifest.json"
PROFILE_PATH = "profile.json"
MAX_PACKAGE_BYTES = 8 * 1024 * 1024
MAX_MANIFEST_BYTES = 256 * 1024
MAX_PROFILE_BYTES = 4 * 1024 * 1024
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def write_role_binding_profile_package(
    document: RoleBindingProfileDocument,
    *,
    strategy_package_hash: str,
    destination: Path,
) -> ProfilePackageArchiveInfo:
    try:
        strategy = ProfileStrategyDependency(
            strategy_id=document.strategy_id,
            strategy_version=document.strategy_version,
            package_hash=strategy_package_hash,
        )
    except ValueError as error:
        raise ProfilePackageError(
            "Profile package dependency metadata is invalid"
        ) from error
    if len(document.canonical_json) > MAX_PROFILE_BYTES:
        raise ProfilePackageError("Profile exceeds the 4 MiB limit")
    profile_value = _json_object(document.canonical_json, "Profile")
    if _canonical_json_bytes(profile_value) != document.canonical_json:
        raise ProfilePackageError("Profile is not canonical JSON")
    _require_profile_identity(document, profile_value)
    manifest_bytes = _canonical_json_bytes(_manifest(document, strategy))

    destination_path = destination.expanduser()
    parent = destination_path.parent.resolve(strict=True)
    destination_path = parent / destination_path.name
    if destination_path.exists() and destination_path.is_dir():
        raise ValueError(
            f"Profile package destination is a directory: {destination_path}"
        )
    staged = parent / f".{destination_path.name}.{uuid.uuid4().hex}.tmp"
    try:
        with zipfile.ZipFile(
            staged,
            mode="x",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as archive:
            _write_entry(archive, PROFILE_PATH, document.canonical_json)
            _write_entry(archive, MANIFEST_PATH, manifest_bytes)
        if staged.stat().st_size > MAX_PACKAGE_BYTES:
            raise ProfilePackageError("Profile package exceeds the 8 MiB limit")
        with staged.open("r+b") as stream:
            os.fsync(stream.fileno())
        os.replace(staged, destination_path)
    finally:
        staged.unlink(missing_ok=True)
    return ProfilePackageArchiveInfo(
        destination_path=str(destination_path),
        package_hash=_file_hash(destination_path),
        byte_size=destination_path.stat().st_size,
    )


def inspect_role_binding_profile_package(source: Path) -> ProfilePackageInspection:
    source_path, package_hash = _validated_source(source)
    try:
        with zipfile.ZipFile(source_path, mode="r") as archive:
            members = _validated_members(archive)
            if set(members) != {MANIFEST_PATH, PROFILE_PATH}:
                raise ProfilePackageError(
                    "Profile package entries do not match the contract"
                )
            manifest_bytes = _read_member(
                archive, members[MANIFEST_PATH], MAX_MANIFEST_BYTES
            )
            manifest = _json_object(manifest_bytes, "Manifest")
            if _canonical_json_bytes(manifest) != manifest_bytes:
                raise ProfilePackageError(
                    "Profile package manifest is not canonical JSON"
                )
            profile_meta, strategy = _validate_manifest(manifest)
            profile_bytes = _read_member(
                archive, members[PROFILE_PATH], MAX_PROFILE_BYTES
            )
            profile_value = _json_object(profile_bytes, "Profile")
            if _canonical_json_bytes(profile_value) != profile_bytes:
                raise ProfilePackageError(
                    "Profile package Profile is not canonical JSON"
                )
            target = _object(profile_value.get("target"), "Profile target")
            raw_hint = profile_value.get("catalog_hint")
            hint = _object(raw_hint, "Profile Catalog hint") if raw_hint else {}
            try:
                document = RoleBindingProfileDocument(
                    profile_id=str(profile_meta["profile_id"]),
                    display_name=str(profile_value.get("display_name")),
                    strategy_id=str(strategy.strategy_id),
                    strategy_version=str(strategy.strategy_version),
                    target_type=str(target.get("target_type")),
                    provider_id=str(target.get("provider_id")),
                    catalog_id=(
                        str(hint["catalog_id"])
                        if "catalog_id" in hint
                        else None
                    ),
                    semantic_hash=str(profile_meta["semantic_hash"]),
                    canonical_json=profile_bytes,
                )
            except ValueError as error:
                raise ProfilePackageError(
                    "Profile package metadata is invalid"
                ) from error
            _require_profile_identity(document, profile_value)
    except zipfile.BadZipFile as error:
        raise ProfilePackageError(
            "Profile package is not a valid ZIP archive"
        ) from error
    if _file_hash(source_path) != package_hash:
        raise ProfilePackageError(
            "Profile package changed while it was being read"
        )
    return ProfilePackageInspection(
        source_path=str(source_path),
        package_hash=package_hash,
        profile=document,
        strategy=strategy,
    )


def _manifest(
    document: RoleBindingProfileDocument,
    strategy: ProfileStrategyDependency,
) -> dict[str, object]:
    return {
        "schema": PACKAGE_SCHEMA,
        "version": PACKAGE_VERSION,
        "profile": {
            "path": PROFILE_PATH,
            "profile_id": document.profile_id,
            "semantic_hash": document.semantic_hash,
        },
        "strategy": {
            "strategy_id": strategy.strategy_id,
            "strategy_version": strategy.strategy_version,
            "package_hash": strategy.package_hash,
        },
    }


def _validate_manifest(
    value: dict[str, object],
) -> tuple[dict[str, object], ProfileStrategyDependency]:
    if set(value) != {"schema", "version", "profile", "strategy"}:
        raise ProfilePackageError("Profile package manifest fields are invalid")
    if value["schema"] != PACKAGE_SCHEMA or value["version"] != PACKAGE_VERSION:
        raise ProfilePackageError("Unsupported Profile package schema or version")
    profile = _object(value["profile"], "Profile metadata")
    if set(profile) != {"path", "profile_id", "semantic_hash"}:
        raise ProfilePackageError(
            "Profile package Profile metadata fields are invalid"
        )
    if profile["path"] != PROFILE_PATH:
        raise ProfilePackageError("Profile package Profile path is invalid")
    strategy_value = _object(value["strategy"], "Strategy dependency")
    if set(strategy_value) != {
        "strategy_id",
        "strategy_version",
        "package_hash",
    }:
        raise ProfilePackageError(
            "Profile package Strategy dependency fields are invalid"
        )
    try:
        strategy = ProfileStrategyDependency(
            strategy_id=str(strategy_value["strategy_id"]),
            strategy_version=str(strategy_value["strategy_version"]),
            package_hash=str(strategy_value["package_hash"]),
        )
    except ValueError as error:
        raise ProfilePackageError(
            "Profile package dependency metadata is invalid"
        ) from error
    return profile, strategy


def _require_profile_identity(
    document: RoleBindingProfileDocument,
    value: dict[str, object],
) -> None:
    strategy = _object(value.get("strategy"), "Profile strategy")
    target = _object(value.get("target"), "Profile target")
    actual = (
        value.get("schema"),
        value.get("version"),
        value.get("profile_id"),
        value.get("display_name"),
        strategy.get("strategy_id"),
        strategy.get("strategy_version"),
        target.get("target_type"),
        target.get("provider_id"),
    )
    expected = (
        "verdflow.role-binding-profile",
        "2",
        document.profile_id,
        document.display_name,
        document.strategy_id,
        document.strategy_version,
        document.target_type,
        document.provider_id,
    )
    if actual != expected:
        raise ProfilePackageError(
            "Profile content does not match package metadata"
        )


def _validated_source(source: Path) -> tuple[Path, str]:
    source_path = source.expanduser().resolve(strict=True)
    if not source_path.is_file():
        raise ValueError(f"Profile package source is not a file: {source_path}")
    if source_path.stat().st_size > MAX_PACKAGE_BYTES:
        raise ProfilePackageError("Profile package exceeds the 8 MiB limit")
    return source_path, _file_hash(source_path)


def _validated_members(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    members: dict[str, zipfile.ZipInfo] = {}
    expanded_size = 0
    for member in archive.infolist():
        path = PurePosixPath(member.filename)
        if member.is_dir() or path.is_absolute() or ".." in path.parts:
            raise ProfilePackageError("Profile package contains an unsafe path")
        normalized = path.as_posix()
        if normalized in members:
            raise ProfilePackageError("Profile package contains duplicate entries")
        expanded_size += member.file_size
        if expanded_size > MAX_PACKAGE_BYTES:
            raise ProfilePackageError(
                "Profile package expands beyond the 8 MiB limit"
            )
        members[normalized] = member
    return members


def _read_member(
    archive: zipfile.ZipFile,
    member: zipfile.ZipInfo,
    limit: int,
) -> bytes:
    if member.file_size > limit:
        raise ProfilePackageError("Profile package entry exceeds its size limit")
    with archive.open(member, mode="r") as stream:
        payload = stream.read(limit + 1)
    if len(payload) > limit:
        raise ProfilePackageError("Profile package entry exceeds its size limit")
    return payload


def _write_entry(archive: zipfile.ZipFile, path: str, payload: bytes) -> None:
    info = zipfile.ZipInfo(path, date_time=_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    archive.writestr(info, payload)


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _json_object(payload: bytes, label: str) -> dict[str, object]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProfilePackageError(
            f"{label} is not valid UTF-8 JSON"
        ) from error
    return _object(value, label)


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ProfilePackageError(f"{label} must be an object")
    return value


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"
