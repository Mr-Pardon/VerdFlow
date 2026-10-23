"""Inspect a strategy package without importing its generator."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any

from verdflow.planting import load_strategy_yaml, semantic_hash

from .errors import StrategyPackageError


MANIFEST_NAME = "strategy-package.json"
PACKAGE_SCHEMA = "verdflow.strategy-package"
PACKAGE_VERSION = "1"
SUPPORTED_RUNTIME = "python"
SUPPORTED_API_VERSION = "1"
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_PACKAGE_BYTES = 64 * 1024 * 1024
IDENTIFIER = re.compile(r"^[a-z][a-z0-9._-]*$")
FUNCTION_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MANIFEST_KEYS = {
    "schema",
    "version",
    "package_id",
    "package_version",
    "runtime",
    "api_version",
    "strategy_file",
    "entry",
    "files",
}


@dataclass(frozen=True)
class StrategyPackage:
    root: Path
    package_id: str
    package_version: str
    runtime: str
    api_version: str
    strategy_file: Path
    entry_file: Path
    entry_function: str
    strategy: dict[str, Any]
    package_hash: str
    file_hashes: tuple[tuple[str, str], ...]


def inspect_strategy_package(path: str | Path) -> StrategyPackage:
    root = Path(path).resolve(strict=True)
    if not root.is_dir():
        raise StrategyPackageError(f"Strategy package is not a directory: {root}")

    manifest_path = root / MANIFEST_NAME
    _require_regular_file(root, manifest_path, MANIFEST_NAME)
    if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
        raise StrategyPackageError("Strategy package manifest is too large")
    manifest = _load_json_object(manifest_path)
    _validate_manifest(manifest)

    listed = tuple(_normalize_relative_path(value) for value in manifest["files"])
    if len(listed) != len(set(listed)):
        raise StrategyPackageError("Strategy package files must be unique")
    if list(listed) != sorted(listed):
        raise StrategyPackageError("Strategy package files must be sorted")

    actual = _actual_distribution_files(root)
    if set(listed) != actual:
        missing = sorted(set(listed) - actual)
        unlisted = sorted(actual - set(listed))
        raise StrategyPackageError(
            f"Strategy package file set mismatch; missing={missing}, unlisted={unlisted}"
        )

    file_hashes: list[tuple[str, str]] = []
    total_bytes = 0
    for relative in listed:
        file_path = root / Path(relative)
        _require_regular_file(root, file_path, relative)
        size = file_path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise StrategyPackageError(f"Strategy package file is too large: {relative}")
        total_bytes += size
        if total_bytes > MAX_PACKAGE_BYTES:
            raise StrategyPackageError("Strategy package declared files are too large")
        file_hashes.append((relative, _sha256_file(file_path)))

    strategy_relative = _normalize_relative_path(manifest["strategy_file"])
    if strategy_relative not in listed:
        raise StrategyPackageError("strategy_file must appear in files")
    entry_file_relative, entry_function = _parse_entry(manifest["entry"])
    if entry_file_relative not in listed:
        raise StrategyPackageError("entry file must appear in files")

    strategy = load_strategy_yaml(
        (root / Path(strategy_relative)).read_text(encoding="utf-8")
    )
    if strategy["strategy_id"] != manifest["package_id"]:
        raise StrategyPackageError("package_id must match strategy_id")
    if strategy["strategy_version"] != manifest["package_version"]:
        raise StrategyPackageError("package_version must match strategy_version")
    if strategy["generator"].get("entry") != manifest["entry"]:
        raise StrategyPackageError("manifest entry must match strategy generator entry")

    normalized_manifest = dict(manifest)
    normalized_manifest["files"] = list(listed)
    identity = {
        "manifest": normalized_manifest,
        "files": [
            {"path": relative, "sha256": digest}
            for relative, digest in file_hashes
        ],
    }
    return StrategyPackage(
        root=root,
        package_id=manifest["package_id"],
        package_version=manifest["package_version"],
        runtime=manifest["runtime"],
        api_version=manifest["api_version"],
        strategy_file=root / Path(strategy_relative),
        entry_file=root / Path(entry_file_relative),
        entry_function=entry_function,
        strategy=strategy,
        package_hash=semantic_hash(identity),
        file_hashes=tuple(file_hashes),
    )


def _load_json_object(path: Path) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise StrategyPackageError(f"Duplicate manifest key: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise StrategyPackageError(f"Invalid strategy package manifest: {error}") from error
    if not isinstance(value, dict):
        raise StrategyPackageError("Strategy package manifest root must be an object")
    return value


def _validate_manifest(value: dict[str, Any]) -> None:
    if set(value) != MANIFEST_KEYS:
        missing = sorted(MANIFEST_KEYS - set(value))
        unknown = sorted(set(value) - MANIFEST_KEYS)
        raise StrategyPackageError(
            f"Strategy package manifest fields mismatch; missing={missing}, unknown={unknown}"
        )
    if value["schema"] != PACKAGE_SCHEMA or value["version"] != PACKAGE_VERSION:
        raise StrategyPackageError("Unsupported strategy package schema or version")
    if not isinstance(value["package_id"], str) or not IDENTIFIER.fullmatch(value["package_id"]):
        raise StrategyPackageError("Invalid strategy package_id")
    if not isinstance(value["package_version"], str) or not value["package_version"]:
        raise StrategyPackageError("Invalid strategy package_version")
    if value["runtime"] != SUPPORTED_RUNTIME:
        raise StrategyPackageError("Only the python strategy runtime is supported")
    if value["api_version"] != SUPPORTED_API_VERSION:
        raise StrategyPackageError("Unsupported strategy package api_version")
    if not isinstance(value["strategy_file"], str):
        raise StrategyPackageError("strategy_file must be a string")
    if not isinstance(value["entry"], str):
        raise StrategyPackageError("entry must be a string")
    if not isinstance(value["files"], list) or not value["files"]:
        raise StrategyPackageError("files must be a non-empty array")
    if not all(isinstance(item, str) for item in value["files"]):
        raise StrategyPackageError("files must contain only strings")


def _normalize_relative_path(value: str) -> str:
    if not value or "\\" in value or ":" in value:
        raise StrategyPackageError(f"Invalid strategy package path: {value!r}")
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise StrategyPackageError(f"Invalid strategy package path: {value!r}")
    return pure.as_posix()


def _parse_entry(value: str) -> tuple[str, str]:
    if value.count(":") != 1:
        raise StrategyPackageError("Strategy entry must use relative.py:function")
    file_value, function_name = value.split(":", 1)
    file_path = _normalize_relative_path(file_value)
    if not file_path.endswith(".py") or not FUNCTION_NAME.fullmatch(function_name):
        raise StrategyPackageError("Strategy entry must use relative.py:function")
    return file_path, function_name


def _actual_distribution_files(root: Path) -> set[str]:
    result: set[str] = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if path.is_symlink():
            raise StrategyPackageError(f"Strategy package cannot contain symlinks: {relative}")
        if path.is_dir():
            continue
        if MANIFEST_NAME == relative.as_posix():
            continue
        if "__pycache__" in relative.parts or path.suffix == ".pyc":
            continue
        result.add(relative.as_posix())
    return result


def _require_regular_file(root: Path, path: Path, label: str) -> None:
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise StrategyPackageError(f"Strategy package cannot contain symlinks: {label}")
    if not path.is_file():
        raise StrategyPackageError(f"Strategy package file is missing: {label}")
    try:
        path.resolve(strict=True).relative_to(root)
    except ValueError as error:
        raise StrategyPackageError(f"Strategy package path escapes root: {label}") from error


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()

