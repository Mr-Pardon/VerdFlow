"""Deterministic internal identifiers for user-library logical records."""

from __future__ import annotations

import hashlib


def stable_id(prefix: str, *parts: str) -> str:
    payload = "\0".join(parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(payload).hexdigest()[:24]}"


def hash_id(prefix: str, semantic_hash: str) -> str:
    return f"{prefix}_{semantic_hash.removeprefix('sha256:')[:24]}"
