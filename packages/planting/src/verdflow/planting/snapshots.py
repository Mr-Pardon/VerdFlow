"""Derive independently persisted snapshots from validated planting facts."""

from __future__ import annotations

import copy
from typing import Any

from .validation import (
    validate_target_asset_use_snapshot,
    validate_effective_binding_snapshot,
    with_semantic_hash,
)


def extract_target_asset_use_snapshot(
    effective_binding_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Extract exact target asset facts used by an effective binding."""
    effective = validate_effective_binding_snapshot(
        copy.deepcopy(effective_binding_snapshot)
    )
    asset_use = effective["asset_use"]
    payload = {
        "schema": "verdflow.target-asset-use-snapshot",
        "version": "1",
        "target": copy.deepcopy(effective["target"]),
        "source": copy.deepcopy(asset_use["source"]),
        "assets": copy.deepcopy(asset_use["assets"]),
    }
    return validate_target_asset_use_snapshot(with_semantic_hash(payload))
