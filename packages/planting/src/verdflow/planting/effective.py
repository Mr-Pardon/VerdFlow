"""Build self-contained target binding facts for one assignment execution."""

from __future__ import annotations

import copy
from typing import Any

from .canonical import semantic_hash
from .errors import PlantingDocumentError
from .validation import (
    validate_catalog,
    validate_effective_binding_snapshot,
    validate_profile,
    validate_strategy,
    with_semantic_hash,
)
from .binding_profiles import complete_profile_assignments, validate_binding_strategy
from .binding_drafts import build_binding_draft
from .selection import role_enabled


DEFAULT_SCALE = {"mode": "fixed", "value": 1.0}


def build_effective_binding_from_profile(
    strategy: dict[str, Any],
    binding: dict[str, Any],
    *,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Freeze a self-contained Profile without reading its source Catalog."""
    strategy = validate_strategy(copy.deepcopy(strategy))
    binding = validate_profile(copy.deepcopy(binding))
    overrides = copy.deepcopy(overrides or {})
    if not isinstance(overrides, dict):
        raise PlantingDocumentError("overrides must be an object keyed by role_key")
    assignments = complete_profile_assignments(strategy, binding)
    for role_key, assignment in overrides.items():
        if role_key not in assignments:
            raise PlantingDocumentError(
                f"overrides contain unknown role {role_key!r}"
            )
        assignments[role_key] = {**assignments[role_key], **assignment}

    validate_binding_strategy(strategy, {**binding, "assignments": assignments})
    original_assignments = {
        key: {**value, "enabled": role_enabled(value)}
        for key, value in binding["assignments"].items()
    }
    normalized_assignments = {
        key: {**value, "enabled": role_enabled(value)} for key, value in assignments.items()
    }

    assets, effective_assignments = _effective_assignments(assignments)
    profile_hash = semantic_hash(binding)
    return _self_contained_effective(
        {"strategy_id": strategy["strategy_id"], "strategy_version": strategy["strategy_version"]},
        binding["target"], assets, effective_assignments,
        source={"kind": "profile", "profile_id": binding["profile_id"], "semantic_hash": profile_hash},
        source_profile={"profile_id": binding["profile_id"], "semantic_hash": profile_hash},
        override_applied=normalized_assignments != original_assignments,
    )


def build_effective_binding_from_draft(
    strategy: dict[str, Any], assignments: dict[str, Any], *, target_type: str,
) -> dict[str, Any]:
    """Freeze unsaved multi-Catalog choices without a synthetic Catalog/Profile."""
    strategy = validate_strategy(copy.deepcopy(strategy))
    draft = build_binding_draft(assignments, target_type=target_type)
    strategy_ref = {"strategy_id": strategy["strategy_id"], "strategy_version": strategy["strategy_version"]}
    validate_binding_strategy(strategy, draft)
    assets, effective_assignments = _effective_assignments(draft["assignments"])
    return _self_contained_effective(
        strategy_ref, draft["target"], assets, effective_assignments,
        source={"kind": "draft", "semantic_hash": semantic_hash(draft)},
        source_profile=None, override_applied=False,
    )


def _effective_assignments(assignments: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    assets: dict[str, dict[str, Any]] = {}
    effective_assignments: dict[str, Any] = {}
    for role_key, assignment in assignments.items():
        if not role_enabled(assignment):
            continue
        candidates: list[dict[str, Any]] = []
        for candidate in assignment["candidates"]:
            target = candidate["target_asset_ref"]
            asset_id = candidate["asset_id"]
            existing = assets.get(asset_id)
            asset = {
                "asset_id": asset_id,
                "display_name": candidate["display_name_snapshot"],
                "provider_metadata": copy.deepcopy(target["locator"]),
            }
            for field in ("reference_radius_m", "reference_height_m"):
                if field in candidate:
                    asset[field] = float(candidate[field])
            if existing is not None and {
                key: value for key, value in existing.items() if key != "catalog_link"
            } != asset:
                raise PlantingDocumentError(
                    f"asset_id {asset_id!r} resolves to multiple target locators"
                )
            if "catalog_link" in candidate:
                asset["catalog_link"] = copy.deepcopy(candidate["catalog_link"])
            assets[asset_id] = asset
            candidates.append(
                {
                    "asset_id": asset_id,
                    "weight": float(candidate["weight"]),
                    "scale": copy.deepcopy(candidate["scale"]),
                }
            )
        effective_assignments[role_key] = {"candidates": candidates}

    return assets, effective_assignments


def _self_contained_effective(
    strategy: dict[str, Any], target: dict[str, Any], assets: dict[str, Any],
    assignments: dict[str, Any], *, source: dict[str, Any],
    source_profile: dict[str, Any] | None, override_applied: bool,
) -> dict[str, Any]:
    payload = {
        "schema": "verdflow.effective-binding-snapshot",
        "version": "1",
        "strategy": copy.deepcopy(strategy),
        "target": copy.deepcopy(target),
        "source_profile": source_profile,
        "override_applied": override_applied,
        "asset_use": {
            "source": source,
            "assets": [assets[key] for key in sorted(assets)],
        },
        "assignments": assignments,
    }
    return validate_effective_binding_snapshot(with_semantic_hash(payload))


def build_effective_binding_snapshot(
    strategy: dict[str, Any],
    catalog: dict[str, Any],
    *,
    catalog_generation: int,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Freeze exact role mappings and selected target asset records."""

    strategy = validate_strategy(copy.deepcopy(strategy))
    catalog = validate_catalog(copy.deepcopy(catalog))
    if (
        not isinstance(catalog_generation, int)
        or isinstance(catalog_generation, bool)
        or catalog_generation < 1
    ):
        raise PlantingDocumentError(
            "catalog_generation must be a positive integer"
        )
    overrides = copy.deepcopy(overrides or {})
    if not isinstance(overrides, dict):
        raise PlantingDocumentError("overrides must be an object keyed by role_key")
    if overrides:
        _validate_overrides(strategy, catalog, overrides)

    strategy_ref = {
        "strategy_id": strategy["strategy_id"],
        "strategy_version": strategy["strategy_version"],
    }
    target_ref = {
        "target_type": catalog["target_type"],
        "provider_id": catalog["provider_id"],
    }
    roles = {item["role_key"]: item for item in strategy["roles"]}
    assignments: dict[str, Any] = {}
    unknown_overrides = sorted(set(overrides) - set(roles))
    if unknown_overrides:
        raise PlantingDocumentError(
            "overrides contain unknown roles: " + ", ".join(unknown_overrides)
        )
    assignments.update(overrides)

    unknown_assignments = sorted(set(assignments) - set(roles))
    if unknown_assignments:
        raise PlantingDocumentError(
            "profile contains unknown roles: " + ", ".join(unknown_assignments)
        )
    missing_required = sorted(
        role_key
        for role_key, role in roles.items()
        if role["required"] and role_key not in assignments
    )
    if missing_required:
        raise PlantingDocumentError(
            "required roles are not mapped: " + ", ".join(missing_required)
        )

    assets = {item["asset_id"]: item for item in catalog["assets"]}
    collections = {
        item["collection_id"]: item for item in catalog.get("collections", [])
    }
    selected_assets: set[str] = set()
    effective_assignments: dict[str, Any] = {}
    for role_key in sorted(assignments):
        assignment = assignments[role_key]
        if not role_enabled(assignment):
            continue
        candidates = _expand_assignment(
            role_key,
            assignment,
            assets,
            collections,
        )
        policy = roles[role_key]["assignment_policy"]
        if policy == "single" and len(candidates) != 1:
            raise PlantingDocumentError(
                f"role {role_key!r} uses single assignment_policy and requires one asset"
            )
        selected_assets.update(item["asset_id"] for item in candidates)
        effective_assignments[role_key] = {"candidates": candidates}

    catalog_assets = [copy.deepcopy(assets[asset_id]) for asset_id in sorted(selected_assets)]
    payload = {
        "schema": "verdflow.effective-binding-snapshot",
        "version": "1",
        "strategy": strategy_ref,
        "target": target_ref,
        "source_profile": None,
        "override_applied": bool(overrides),
        "asset_use": {
            "source": {
                "kind": "catalog",
                "catalog_id": catalog["catalog_id"],
                "generation": catalog_generation,
                "semantic_hash": semantic_hash(catalog),
            },
            "assets": catalog_assets,
        },
        "assignments": effective_assignments,
    }
    return validate_effective_binding_snapshot(with_semantic_hash(payload))


def _validate_overrides(
    strategy: dict[str, Any],
    catalog: dict[str, Any],
    overrides: dict[str, Any],
) -> None:
    # Manual execution overrides use Catalog asset ids and are validated while
    # they are expanded below; they are not persisted Profiles.
    if not isinstance(overrides, dict):
        raise PlantingDocumentError("overrides must be an object")


def _expand_assignment(
    role_key: str,
    assignment: dict[str, Any],
    assets: dict[str, dict[str, Any]],
    collections: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    if "collection_id" in assignment:
        collection_id = assignment["collection_id"]
        collection = collections.get(collection_id)
        if collection is None:
            raise PlantingDocumentError(
                f"role {role_key!r} references unknown collection {collection_id!r}"
            )
        raw_candidates = [
            {"asset_id": asset_id, "weight": 1.0}
            for asset_id in collection["asset_ids"]
        ]
    else:
        raw_candidates = assignment["candidates"]

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_candidates:
        asset_id = raw["asset_id"]
        if asset_id in seen:
            raise PlantingDocumentError(
                f"role {role_key!r} maps asset {asset_id!r} more than once"
            )
        asset = assets.get(asset_id)
        if asset is None:
            raise PlantingDocumentError(
                f"role {role_key!r} references unknown asset {asset_id!r}"
            )
        seen.add(asset_id)
        scale = (
            raw.get("scale")
            or assignment.get("scale")
            or asset.get("suggested_scale")
            or DEFAULT_SCALE
        )
        result.append(
            {
                "asset_id": asset_id,
                "weight": float(raw.get("weight", 1.0)),
                "scale": copy.deepcopy(scale),
            }
        )
    return result
