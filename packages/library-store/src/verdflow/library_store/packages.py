"""Immutable managed copies of verified strategy package directories."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid

from verdflow.contracts import require_package_hash

from .errors import LibraryIntegrityError, StrategyInstallConflictError
from .layout import LibraryLayout


MAX_FILE_COUNT = 1024
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_PACKAGE_BYTES = 64 * 1024 * 1024


class ManagedStrategyPackages:
    def __init__(self, layout: LibraryLayout) -> None:
        self._layout = layout

    def stage_copy(self, source_root: Path) -> tuple[Path, str]:
        source = source_root.resolve(strict=True)
        if not source.is_dir():
            raise ValueError(f"Strategy package is not a directory: {source}")
        destination = self._layout.staging / uuid.uuid4().hex
        destination.mkdir(parents=True)
        try:
            files = _distribution_files(source)
            for relative in files:
                source_file = source / Path(relative)
                target_file = destination / Path(relative)
                target_file.parent.mkdir(parents=True, exist_ok=True)
                with source_file.open("rb") as input_stream:
                    with target_file.open("xb") as output_stream:
                        shutil.copyfileobj(input_stream, output_stream)
                        output_stream.flush()
                        os.fsync(output_stream.fileno())
            return destination, _payload_hash(destination)
        except Exception:
            shutil.rmtree(destination, ignore_errors=True)
            raise

    def commit(self, staged: Path, package_hash: str, payload_hash: str) -> Path:
        require_package_hash(package_hash)
        destination = self.path_for_hash(package_hash)
        if destination.exists():
            existing_hash = _payload_hash(destination)
            shutil.rmtree(staged, ignore_errors=True)
            if existing_hash != payload_hash:
                raise StrategyInstallConflictError(
                    "Managed bytes differ for an existing strategy package hash"
                )
            return destination
        try:
            os.replace(staged, destination)
        except OSError:
            if not destination.is_dir():
                raise
            existing_hash = _payload_hash(destination)
            shutil.rmtree(staged, ignore_errors=True)
            if existing_hash != payload_hash:
                raise StrategyInstallConflictError(
                    "Managed bytes differ for an existing strategy package hash"
                )
        return destination

    def verify(self, relative_path: str, expected_payload_hash: str) -> Path:
        path = (self._layout.root / relative_path).resolve()
        try:
            path.relative_to(self._layout.strategy_packages.resolve())
        except ValueError as error:
            raise LibraryIntegrityError(
                "Strategy package path escapes the managed library"
            ) from error
        if not path.is_dir():
            raise LibraryIntegrityError(f"Managed strategy package is missing: {path}")
        actual = _payload_hash(path)
        if actual != expected_payload_hash:
            raise LibraryIntegrityError(
                f"Managed strategy package payload changed: {path}"
            )
        return path

    def path_for_hash(self, package_hash: str) -> Path:
        require_package_hash(package_hash)
        return self._layout.strategy_packages / package_hash.removeprefix("sha256:")

    def remove_unregistered(
        self,
        relative_path: str,
        expected_payload_hash: str,
    ) -> None:
        path = self.verify(relative_path, expected_payload_hash)
        shutil.rmtree(path, ignore_errors=True)

    def cleanup_staging(self) -> int:
        removed = 0
        for path in self._layout.staging.iterdir():
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            removed += 1
        return removed


def _distribution_files(root: Path) -> tuple[str, ...]:
    files: list[str] = []
    total_bytes = 0
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if path.is_symlink():
            raise ValueError(f"Strategy package cannot contain symlinks: {relative}")
        if path.is_dir():
            continue
        if "__pycache__" in relative.parts or path.suffix == ".pyc":
            continue
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise ValueError(f"Strategy package file is too large: {relative}")
        total_bytes += size
        if total_bytes > MAX_PACKAGE_BYTES:
            raise ValueError("Strategy package is too large")
        files.append(relative.as_posix())
        if len(files) > MAX_FILE_COUNT:
            raise ValueError("Strategy package contains too many files")
    if not files:
        raise ValueError("Strategy package is empty")
    return tuple(sorted(files))


def _payload_hash(root: Path) -> str:
    identity = []
    for relative in _distribution_files(root):
        path = root / Path(relative)
        identity.append(
            {
                "path": relative,
                "size": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    payload = json.dumps(
        identity,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
