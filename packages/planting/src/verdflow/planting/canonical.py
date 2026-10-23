"""Canonical JSON encoding used for Phase 2A semantic identities."""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from typing import Any

from .errors import PlantingDocumentError


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


def semantic_hash(value: Any) -> str:
    """Return a content identity for normalized semantic data."""

    digest = hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    return f"sha256:{digest}"


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
