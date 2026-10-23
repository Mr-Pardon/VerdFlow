"""Deterministic export of bundled, read-only Agent authoring kits."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from importlib.resources import files
import os
from pathlib import Path
from typing import Iterable
import uuid
import zipfile


AUTHORING_KIT_MEDIA_TYPE = "application/vnd.verdflow.authoring-kit+zip"
SUPPORTED_KITS = ("vfa-author", "vfs-author")
MAX_KIT_FILES = 128
MAX_KIT_FILE_BYTES = 2 * 1024 * 1024
MAX_KIT_BYTES = 16 * 1024 * 1024
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


@dataclass(frozen=True, slots=True)
class AuthoringKitArchiveInfo:
    skill_id: str
    destination_path: str
    archive_hash: str
    byte_size: int


def available_authoring_kits() -> tuple[str, ...]:
    return SUPPORTED_KITS


def write_authoring_kit(
    skill_id: str,
    destination: Path,
) -> AuthoringKitArchiveInfo:
    if skill_id not in SUPPORTED_KITS:
        raise ValueError(f"Unknown VerdFlow authoring kit: {skill_id}")
    destination_path = destination.expanduser()
    parent = destination_path.parent.resolve(strict=True)
    destination_path = parent / destination_path.name
    if destination_path.exists() and destination_path.is_dir():
        raise ValueError(f"Authoring kit destination is a directory: {destination_path}")

    root = files("verdflow.authoring_kits").joinpath("kits", skill_id)
    entries = list(_resource_entries(root))
    if skill_id == "vfs-author":
        entries.append((
            "assets/strategy_versions.py",
            files("verdflow.contracts").joinpath("strategy_versions.py").read_bytes(),
        ))
    entries = tuple(sorted(entries))
    if not entries or len(entries) > MAX_KIT_FILES:
        raise ValueError("Authoring kit has an invalid file count")
    if "SKILL.md" not in {relative for relative, _ in entries}:
        raise ValueError("Authoring kit is missing SKILL.md")

    staged = parent / f".{destination_path.name}.{uuid.uuid4().hex}.tmp"
    try:
        total_bytes = 0
        with zipfile.ZipFile(staged, mode="x", compression=zipfile.ZIP_STORED) as archive:
            for relative, payload in entries:
                if len(payload) > MAX_KIT_FILE_BYTES:
                    raise ValueError(f"Authoring kit file is too large: {relative}")
                total_bytes += len(payload)
                if total_bytes > MAX_KIT_BYTES:
                    raise ValueError("Authoring kit exceeds the size limit")
                info = zipfile.ZipInfo(
                    f"{skill_id}/{relative}",
                    date_time=_ZIP_TIMESTAMP,
                )
                info.compress_type = zipfile.ZIP_STORED
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                archive.writestr(info, payload)
        with staged.open("r+b") as stream:
            os.fsync(stream.fileno())
        _verify_archive(staged, skill_id, entries)
        os.replace(staged, destination_path)
    finally:
        staged.unlink(missing_ok=True)

    return AuthoringKitArchiveInfo(
        skill_id=skill_id,
        destination_path=str(destination_path),
        archive_hash=_file_hash(destination_path),
        byte_size=destination_path.stat().st_size,
    )


def _resource_entries(root) -> Iterable[tuple[str, bytes]]:
    def walk(current, prefix: str) -> Iterable[tuple[str, bytes]]:
        for child in sorted(current.iterdir(), key=lambda item: item.name):
            if child.name == "__pycache__" or child.name.endswith(".pyc"):
                continue
            relative = f"{prefix}/{child.name}" if prefix else child.name
            if child.is_dir():
                yield from walk(child, relative)
            elif child.is_file():
                yield relative, child.read_bytes()

    yield from walk(root, "")


def _verify_archive(
    path: Path,
    skill_id: str,
    entries: tuple[tuple[str, bytes], ...],
) -> None:
    expected = {
        f"{skill_id}/{relative}": hashlib.sha256(payload).hexdigest()
        for relative, payload in entries
    }
    with zipfile.ZipFile(path, mode="r") as archive:
        names = archive.namelist()
        if names != sorted(expected) or len(names) != len(set(names)):
            raise ValueError("Authoring kit archive entries are invalid")
        for name in names:
            if hashlib.sha256(archive.read(name)).hexdigest() != expected[name]:
                raise ValueError(f"Authoring kit read-back failed: {name}")


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"
