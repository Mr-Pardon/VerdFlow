"""Canonical JSON encoding used for Phase 2A semantic identities."""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from collections.abc import Iterator
from typing import Any

from .errors import PlantingDocumentError


_ROW_ENCODER = json.JSONEncoder(ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def canonical_json_bytes(value: Any) -> bytes:
    """Encode a JSON-compatible value using VerdFlow canonical JSON v1."""

    normalized = _normalize(value, path="$", seen=set())
    try:
        text = json.dumps(
            normalized,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise PlantingDocumentError(f"Cannot canonicalize document: {error}") from error
    return text.encode("utf-8")


def canonical_row_bytes(value: dict[str, Any]) -> bytes:
    """Encode flat records and short numeric vectors without recursive path tracking."""
    if type(value) is not dict:
        return canonical_json_bytes(value)
    normalized = {}
    for key, item in value.items():
        if type(key) is not str or not key.isascii():
            return canonical_json_bytes(value)
        item_type = type(item)
        if item_type is str:
            item = unicodedata.normalize("NFC", item)
        elif item_type is float:
            item = 0.0 if item == 0.0 else item
        elif item_type is list and len(item) <= 4:
            vector = []
            for number in item:
                number_type = type(number)
                if number_type is float:
                    vector.append(0.0 if number == 0.0 else number)
                elif number_type is int:
                    vector.append(number)
                else:
                    return canonical_json_bytes(value)
            item = vector
        elif item_type is not int and item_type is not bool and item is not None:
            return canonical_json_bytes(value)
        normalized[key] = item
    try:
        return _ROW_ENCODER.encode(normalized).encode("utf-8")
    except (TypeError, ValueError):
        # Recover the existing diagnostic path for non-finite or invalid values.
        return canonical_json_bytes(value)


def semantic_hash(value: Any) -> str:
    """Return a content identity for normalized semantic data."""

    digest = hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    return f"sha256:{digest}"


def canonical_array_chunks(value: dict[str, Any], array_key: str) -> Iterator[bytes]:
    """Encode a canonical object without making a normalized copy of its row array."""
    rows = value.get(array_key)
    if not isinstance(rows, list):
        raise PlantingDocumentError(f"{array_key} must be an array")
    keys: dict[str, str] = {}
    for key in value:
        if not isinstance(key, str):
            raise PlantingDocumentError("$ requires string object keys")
        normalized = unicodedata.normalize("NFC", key)
        if normalized in keys:
            raise PlantingDocumentError(f"$ contains duplicate normalized key {normalized!r}")
        keys[normalized] = key
    yield b"{"
    for index, (key, original) in enumerate(sorted(keys.items())):
        if index:
            yield b","
        yield canonical_json_bytes(key) + b":"
        if original != array_key:
            yield canonical_json_bytes(value[original])
            continue
        yield b"["
        for start in range(0, len(rows), 512):
            if start:
                yield b","
            yield canonical_json_bytes(rows[start:start + 512])[1:-1]
        yield b"]"
    yield b"}"


def canonical_array_hash(value: dict[str, Any], array_key: str) -> str:
    digest = hashlib.sha256()
    for chunk in canonical_array_chunks(value, array_key):
        digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


class CanonicalRowHasher:
    """Hash the existing canonical document as its array rows pass through."""

    def __init__(self, array_key: str, leading_fields: dict[str, Any]) -> None:
        self._array_key = array_key
        self.prefix = self._prefix(leading_fields)
        self._digest = hashlib.sha256(self.prefix)
        self._has_rows = False

    def row(self, value: dict[str, Any]) -> bytes:
        data = (b"," if self._has_rows else b"") + canonical_row_bytes(value)
        self._digest.update(data)
        self._has_rows = True
        return data

    def finish(self, header: dict[str, Any]) -> str:
        if self.prefix != self._prefix(header):
            raise PlantingDocumentError("Canonical row document header differs from its input")
        digest = self._digest.copy()
        digest.update(self.suffix(header))
        return f"sha256:{digest.hexdigest()}"

    def suffix(self, header: dict[str, Any]) -> bytes:
        return b"]" + b"".join(
            b"," + canonical_json_bytes(key) + b":" + canonical_json_bytes(header[key])
            for key in sorted(header) if key > self._array_key
        ) + b"}"

    def _prefix(self, header: dict[str, Any]) -> bytes:
        return b"{" + b"".join(
            canonical_json_bytes(key) + b":" + canonical_json_bytes(header[key]) + b","
            for key in sorted(header) if key < self._array_key
        ) + canonical_json_bytes(self._array_key) + b":["


def point_set_row_hasher(*, kind: str, count: int) -> CanonicalRowHasher:
    return CanonicalRowHasher("points", {
        "coordinate_space": "source_scene_world", "kind": kind,
        "offset_applied": False, "point_count": count,
    })


def _normalize(value: Any, *, path: str, seen: set[int]) -> Any:
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise PlantingDocumentError(f"{path} must contain only finite numbers")
        return 0.0 if value == 0.0 else value
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, list):
        return _normalize_container(value, path=path, seen=seen, mapping=False)
    if isinstance(value, dict):
        return _normalize_container(value, path=path, seen=seen, mapping=True)
    raise PlantingDocumentError(
        f"{path} contains non-JSON value {type(value).__name__}"
    )


def _normalize_container(
    value: list[Any] | dict[str, Any],
    *,
    path: str,
    seen: set[int],
    mapping: bool,
) -> Any:
    identity = id(value)
    if identity in seen:
        raise PlantingDocumentError(f"{path} contains a cyclic reference")
    seen.add(identity)
    try:
        if not mapping:
            return [
                _normalize(item, path=f"{path}[{index}]", seen=seen)
                for index, item in enumerate(value)
            ]

        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise PlantingDocumentError(f"{path} requires string object keys")
            normalized_key = unicodedata.normalize("NFC", key)
            if normalized_key in result:
                raise PlantingDocumentError(
                    f"{path} contains duplicate normalized key {normalized_key!r}"
                )
            result[normalized_key] = _normalize(
                item,
                path=f"{path}.{normalized_key}",
                seen=seen,
            )
        return result
    finally:
        seen.remove(identity)
