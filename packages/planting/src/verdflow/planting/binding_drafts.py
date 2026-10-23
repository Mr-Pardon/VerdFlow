"""Catalog-qualified authoring identities and self-contained, unsaved choices."""

from __future__ import annotations

import copy
from typing import Any

from .binding_profiles import catalog_asset_hashes
from .canonical import semantic_hash
from .errors import PlantingDocumentError
from .validation import validate_binding_draft, validate_profile


def catalog_binding_candidate(
    catalog: dict[str, Any], asset: dict[str, Any],
) -> dict[str, Any]:
    """Project one Catalog asset into a portable candidate with an opaque ID."""
    identity = {key: catalog[key] for key in ("target_type", "provider_id", "catalog_id")}
    identity["asset_id"] = asset["asset_id"]
    target_hash, metadata_hash = catalog_asset_hashes(catalog, asset)
    candidate = {
        "asset_id": "binding_" + semantic_hash(identity).removeprefix("sha256:"),
        "display_name_snapshot": asset["display_name"],
        "target_asset_ref": {
            "target_type": catalog["target_type"],
            "provider_id": catalog["provider_id"],
            "locator": copy.deepcopy(asset["provider_metadata"]),
        },
        "catalog_link": {
            "catalog_id": catalog["catalog_id"],
            "asset_id": asset["asset_id"],
            "observed_target_hash": target_hash,
            "observed_metadata_hash": metadata_hash,
        },
        "weight": 1.0,
        "scale": copy.deepcopy(
            asset.get("suggested_scale") or {"mode": "fixed", "value": 1.0}
        ),
    }
    for field in ("reference_radius_m", "reference_height_m"):
        if field in asset:
            candidate[field] = asset[field]
    return candidate


def build_binding_draft(assignments: dict[str, Any], *, target_type: str) -> dict[str, Any]:
    """Freeze only selected facts; source Catalogs are not execution dependencies."""
    if not isinstance(assignments, dict):
        raise PlantingDocumentError("Binding assignments must be an object")
    provider_ids = {
        candidate["target_asset_ref"].get("provider_id")
        for assignment in assignments.values()
        if isinstance(assignment, dict) and isinstance(assignment.get("candidates"), list)
        for candidate in assignment["candidates"] if isinstance(candidate, dict)
        and isinstance(candidate.get("target_asset_ref"), dict)
        and isinstance(candidate["target_asset_ref"].get("provider_id"), str)
    }
    if len(provider_ids) != 1 or not next(iter(provider_ids), None):
        raise PlantingDocumentError("Binding choices require one compatible target Provider")
    draft = validate_binding_draft(
        {
            "schema": "verdflow.role-binding-draft",
            "version": "1",
            "target": {
                "target_type": target_type,
                "provider_id": next(iter(provider_ids)),
            },
            "assignments": copy.deepcopy(assignments),
        }
    )
    for assignment in draft["assignments"].values():
        assignment.setdefault("enabled", True)
    return draft


def profile_from_binding_draft(
    draft: dict[str, Any],
    *,
    profile_id: str,
    display_name: str,
    strategy_id: str,
    strategy_version: str,
) -> dict[str, Any]:
    draft = validate_binding_draft(copy.deepcopy(draft))
    catalog_ids = {
        candidate["catalog_link"]["catalog_id"]
        for assignment in draft["assignments"].values()
        for candidate in assignment["candidates"] if "catalog_link" in candidate
    }
    profile = {
        "schema": "verdflow.role-binding-profile",
        "version": "2",
        "profile_id": profile_id,
        "display_name": display_name,
        "strategy": {
            "strategy_id": strategy_id,
            "strategy_version": strategy_version,
        },
        "target": draft["target"],
        "assignments": draft["assignments"],
    }
    if len(catalog_ids) == 1:
        profile["catalog_hint"] = {"catalog_id": next(iter(catalog_ids))}
    return validate_profile(profile)
