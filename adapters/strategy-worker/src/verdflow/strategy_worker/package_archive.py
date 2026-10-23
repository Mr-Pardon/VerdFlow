"""Deterministic and bounded archive IO for strategy packages."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import stat
import tempfile
from typing import Iterator
import uuid
import zipfile

from .errors import StrategyPackageError
from .package import (
    MANIFEST_NAME,
    MAX_FILE_BYTES,
    MAX_MANIFEST_BYTES,
    MAX_PACKAGE_BYTES,
    StrategyPackage,
    inspect_strategy_package,
)


MAX_ARCHIVE_BYTES = MAX_PACKAGE_BYTES + MAX_MANIFEST_BYTES + 2 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 1024
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


@dataclass(frozen=True, slots=True)
class StrategyPackageArchiveInfo:
    destination_path: str
    archive_hash: str
    byte_size: int
    package_hash: str


@dataclass(frozen=True, slots=True)
class OpenStrategyPackageArchive:
    source_path: str
    archive_hash: str
    byte_size: int
    package: StrategyPackage


def package_archive_bytes(package: StrategyPackage) -> bytes:
    """Return the stable ZIP payload used by project strategy snapshots."""

    current = _require_unchanged_package(package)
    return _archive_bytes(current)


def write_strategy_package_archive(
    package: StrategyPackage,
    destination: Path,
) -> StrategyPackageArchiveInfo:
    """Write and read back one portable ``.vfs`` strategy archive."""

    current = _require_unchanged_package(package)
    destination_path = destination.expanduser()
    parent = destination_path.parent.resolve(strict=True)
    destination_path = parent / destination_path.name
    if destination_path.exists() and destination_path.is_dir():
        raise ValueError(
            f"Strategy package destination is a directory: {destination_path}"
        )
    staged = parent / f".{destination_path.name}.{uuid.uuid4().hex}.tmp"
    try:
        payload = _archive_bytes(current)
        if len(payload) > MAX_ARCHIVE_BYTES:
            raise StrategyPackageError("Strategy package archive is too large")
        with staged.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        with open_strategy_package_archive(staged) as opened:
            if opened.package.package_hash != current.package_hash:
                raise StrategyPackageError(
                    "Strategy package archive failed read-back validation"
                )
        os.replace(staged, destination_path)
    finally:
        staged.unlink(missing_ok=True)

    return StrategyPackageArchiveInfo(
        destination_path=str(destination_path),
        archive_hash=_file_hash(destination_path),
        byte_size=destination_path.stat().st_size,
        package_hash=current.package_hash,
    )


@contextmanager
def open_strategy_package_archive(
    source: Path,
) -> Iterator[OpenStrategyPackageArchive]:
    """Safely materialize and inspect one portable strategy archive."""

    source_path = source.expanduser().resolve(strict=True)
    if not source_path.is_file():
        raise ValueError(f"Strategy package archive is not a file: {source_path}")
    byte_size = source_path.stat().st_size
    if byte_size <= 0 or byte_size > MAX_ARCHIVE_BYTES:
        raise StrategyPackageError("Strategy package archive has an invalid size")
    archive_hash = _file_hash(source_path)

    with tempfile.TemporaryDirectory(prefix="verdflow-vfs-") as temporary:
        root = Path(temporary) / "package"
        root.mkdir()
        try:
            with zipfile.ZipFile(source_path, mode="r") as archive:
                members = _validated_members(archive)
                for name, info in members.items():
                    target = root / Path(name)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    limit = (
                        MAX_MANIFEST_BYTES
                        if name == MANIFEST_NAME
                        else MAX_FILE_BYTES
                    )
                    with archive.open(info, mode="r") as member:
                        payload = member.read(limit + 1)
                    if len(payload) > limit or len(payload) != info.file_size:
                        raise StrategyPackageError(
                            f"Strategy package archive entry size mismatch: {name}"
                        )
                    target.write_bytes(payload)
        except zipfile.BadZipFile as error:
            raise StrategyPackageError(
                "Strategy package is not a valid ZIP archive"
            ) from error

        package = inspect_strategy_package(root)
        if _file_hash(source_path) != archive_hash:
            raise StrategyPackageError(
                "Strategy package archive changed while it was being read"
            )
        yield OpenStrategyPackageArchive(
            source_path=str(source_path),
            archive_hash=archive_hash,
            byte_size=byte_size,
            package=package,
        )


def _archive_bytes(package: StrategyPackage) -> bytes:
    expected = dict(package.file_hashes)
    paths = (MANIFEST_NAME, *sorted(expected))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for relative in paths:
            path = package.root / Path(relative)
            content = path.read_bytes()
            expected_hash = expected.get(relative)
            if (
                expected_hash is not None
                and hashlib.sha256(content).hexdigest() != expected_hash
            ):
                raise StrategyPackageError(
                    f"Strategy package changed while archiving: {relative}"
                )
            info = zipfile.ZipInfo(relative, date_time=_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
    return buffer.getvalue()


def _validated_members(
    archive: zipfile.ZipFile,
) -> dict[str, zipfile.ZipInfo]:
    infos = archive.infolist()
    if not infos or len(infos) > MAX_ARCHIVE_ENTRIES:
        raise StrategyPackageError(
            "Strategy package archive has an invalid entry count"
        )
    members: dict[str, zipfile.ZipInfo] = {}
    expanded_bytes = 0
    for info in infos:
        name = info.filename
        path = PurePosixPath(name)
        unix_mode = info.external_attr >> 16
        if (
            info.is_dir()
            or not name
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
            or "\\" in name
            or ":" in name
            or stat.S_ISLNK(unix_mode)
        ):
            raise StrategyPackageError(
                f"Unsafe strategy package archive entry: {name!r}"
            )
        if name in members:
            raise StrategyPackageError(
                f"Duplicate strategy package archive entry: {name}"
            )
        if info.flag_bits & 0x1:
            raise StrategyPackageError(
                "Encrypted strategy package entries are not supported"
            )
        per_file_limit = (
            MAX_MANIFEST_BYTES if name == MANIFEST_NAME else MAX_FILE_BYTES
        )
        if info.file_size < 0 or info.file_size > per_file_limit:
            raise StrategyPackageError(
                f"Strategy package archive entry is too large: {name}"
            )
        expanded_bytes += info.file_size
        if expanded_bytes > MAX_PACKAGE_BYTES + MAX_MANIFEST_BYTES:
            raise StrategyPackageError(
                "Strategy package archive expands beyond the size limit"
            )
        members[name] = info
    if MANIFEST_NAME not in members:
        raise StrategyPackageError(
            f"Strategy package archive is missing {MANIFEST_NAME}"
        )
    return members


def _require_unchanged_package(package: StrategyPackage) -> StrategyPackage:
    current = inspect_strategy_package(package.root)
    if current.package_hash != package.package_hash:
        raise StrategyPackageError("Strategy package changed after inspection")
    return current


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"
