"""Build self-contained role bindings and compare optional Catalog hints."""

from __future__ import annotations

import copy
from typing import Any
from verdflow.contracts import strategy_version_accepts

from .canonical import semantic_hash
from .errors import PlantingDocumentError
from .parameters import validate_profile_parameters
from .selection import role_enabled
from .validation import validate_catalog, validate_profile, validate_binding_draft


DEFAULT_SCALE = {"mode": "fixed", "value": 1.0}


def build_profile_v2(
    *,
    profile_id: str,
    display_name: str,
    strategy_id: str,
    strategy_version: str,
    catalog: dict[str, Any],
    assignments: dict[str, Any],
    base_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Copy new Catalog choices while preserving existing executable facts."""
    catalog = validate_catalog(copy.deepcopy(catalog))
    base_candidates = _base_candidates(base_profile)
    assets = {item["asset_id"]: item for item in catalog["assets"]}
    collections = {
        item["collection_id"]: item for item in catalog.get("collections", [])
    }
    normalized: dict[str, Any] = {}
    for role_key, assignment in assignments.items():
        if not isinstance(assignment, dict):
            raise PlantingDocumentError(
                f"assignments.{role_key} must be an object"
            )
        candidates = _candidate_inputs(role_key, assignment, collections)
        normalized[role_key] = {
            "enabled": role_enabled(assignment),
            "candidates": [
                _copy_candidate(
                    role_key,
                    raw,
                    assignment,
                    catalog,
                    assets,
                    base_candidates,
                )
                for raw in candidates
            ]
        }
    return validate_profile(
        {
            "schema": "verdflow.role-binding-profile",
            "version": "2",
            "profile_id": profile_id,
            "display_name": display_name,
            "strategy": {
                "strategy_id": strategy_id,
                "strategy_version": strategy_version,
            },
            "target": {
                "target_type": catalog["target_type"],
                "provider_id": catalog["provider_id"],
            },
            "catalog_hint": {"catalog_id": catalog["catalog_id"]},
            "assignments": normalized,
            **({"strategy_parameters": copy.deepcopy(base_profile["strategy_parameters"])}
               if base_profile is not None and "strategy_parameters" in base_profile else {}),
        }
    )


def catalog_asset_hashes(
    catalog: dict[str, Any],
    asset: dict[str, Any],
) -> tuple[str, str]:
    target_hash = semantic_hash(
        {
            "target_type": catalog["target_type"],
            "provider_id": catalog["provider_id"],
            "locator": asset["provider_metadata"],
        }
    )
    metadata = {
        "display_name": asset["display_name"],
        "thumbnail": asset.get("thumbnail"),
        "lod": asset.get("lod"),
        "suggested_scale": asset.get("suggested_scale"),
        "tags": asset.get("tags", []),
    }
    for field in ("reference_radius_m", "reference_height_m"):
        if field in asset:
            metadata[field] = asset[field]
    metadata_hash = semantic_hash(metadata)
    return target_hash, metadata_hash


def update_profile_v2(
    base_profile: dict[str, Any],
    *,
    display_name: str,
    assignments: dict[str, Any],
) -> dict[str, Any]:
    """Update user controls without consulting the optional source Catalog."""
    base = validate_profile(copy.deepcopy(base_profile))
    candidates = _base_candidates(base)
    normalized: dict[str, Any] = {}
    for role_key, assignment in assignments.items():
        if not isinstance(assignment, dict):
            raise PlantingDocumentError(
                f"assignments.{role_key} must be an object"
            )
        raw_candidates = assignment.get("candidates")
        if not isinstance(raw_candidates, list) or (role_enabled(assignment) and not raw_candidates):
            raise PlantingDocumentError(
                f"assignments.{role_key}.candidates must be non-empty"
            )
        updated: list[dict[str, Any]] = []
        for raw in raw_candidates:
            if not isinstance(raw, dict):
                raise PlantingDocumentError(
                    f"assignments.{role_key}.candidates must contain objects"
                )
            asset_id = raw.get("asset_id")
            current = raw if "target_asset_ref" in raw else candidates.get((role_key, asset_id))
            if current is None:
                raise PlantingDocumentError(
                    f"asset {asset_id!r} needs a Catalog before it can be added"
                )
            candidate = copy.deepcopy(current)
            candidate["weight"] = float(raw.get("weight", current["weight"]))
            candidate["scale"] = copy.deepcopy(
                raw.get("scale") or current["scale"]
            )
            updated.append(candidate)
        normalized[role_key] = {"enabled": role_enabled(assignment), "candidates": updated}
    base["display_name"] = display_name
    base["assignments"] = normalized
    return validate_profile(base)


def binding_catalog_drift(
    binding: dict[str, Any],
    catalog: dict[str, Any] | list[dict[str, Any]] | None,
) -> dict[str, str]:
    """Return candidate status keyed by ``role_key/asset_id``."""
    result: dict[str, str] = {}
    sources = catalog if isinstance(catalog, list) else ([catalog] if catalog is not None else [])
    catalogs = {item["catalog_id"]: item for item in sources}
    asset_maps = {item["catalog_id"]: {asset["asset_id"]: asset for asset in item["assets"]} for item in sources}
    for role_key, assignment in binding["assignments"].items():
        for candidate in assignment["candidates"]:
            key = f"{role_key}/{candidate['asset_id']}"
            link = candidate.get("catalog_link")
            if link is None:
                result[key] = "unlinked"
                continue
            source = catalogs.get(link["catalog_id"])
            if source is None:
                result[key] = "missing"
                continue
            asset = asset_maps[link["catalog_id"]].get(link["asset_id"])
            if asset is None:
                result[key] = "missing"
                continue
            target_hash, metadata_hash = catalog_asset_hashes(source, asset)
            if target_hash != link["observed_target_hash"]:
                result[key] = "target_changed"
            elif metadata_hash != link["observed_metadata_hash"]:
                result[key] = "metadata_changed"
            else:
                result[key] = "current"
    return result


def sync_profile_catalog_metadata(
    binding: dict[str, Any],
    catalog: dict[str, Any],
) -> dict[str, Any]:
    """Refresh linked display metadata without changing executable targets."""
    profile = validate_profile(copy.deepcopy(binding))
    source = validate_catalog(copy.deepcopy(catalog))
    if profile.get("catalog_hint", {}).get("catalog_id") != source["catalog_id"] and not any(
        candidate.get("catalog_link", {}).get("catalog_id") == source["catalog_id"]
        for assignment in profile["assignments"].values() for candidate in assignment["candidates"]
    ):
        raise PlantingDocumentError(
            "Profile is not linked to the supplied Catalog"
        )
    if (
        profile["target"]["target_type"] != source["target_type"]
        or profile["target"]["provider_id"] != source["provider_id"]
    ):
        raise PlantingDocumentError(
            "Profile target does not match the supplied Catalog"
        )

    assets = {item["asset_id"]: item for item in source["assets"]}
    for assignment in profile["assignments"].values():
        for candidate in assignment["candidates"]:
            link = candidate.get("catalog_link")
            if not isinstance(link, dict):
                continue
            if link.get("catalog_id") != source["catalog_id"]:
                continue
            asset = assets.get(link["asset_id"])
            if asset is None:
                continue
            target_hash, metadata_hash = catalog_asset_hashes(source, asset)
            if target_hash != link["observed_target_hash"]:
                continue
            candidate["display_name_snapshot"] = asset["display_name"]
            for field in ("reference_radius_m", "reference_height_m"):
                if field in asset:
                    candidate[field] = float(asset[field])
                else:
                    candidate.pop(field, None)
            link["observed_metadata_hash"] = metadata_hash
    return validate_profile(profile)


def complete_profile_assignments(
    strategy: dict[str, Any], profile: dict[str, Any],
) -> dict[str, Any]:
    """Absent optional roles in a reusable plan are never implicitly enabled."""
    validate_binding_strategy(strategy, profile)
    assignments = copy.deepcopy(profile["assignments"])
    for role in strategy["roles"]:
        assignments.setdefault(role["role_key"], {"enabled": False, "candidates": []})
    return assignments


def validate_binding_strategy(
    strategy: dict[str, Any],
    binding: dict[str, Any],
) -> None:
    """Validate Role coverage without consulting a Catalog."""
    is_draft = binding.get("schema") == "verdflow.role-binding-draft"
    binding = (validate_binding_draft if is_draft else validate_profile)(copy.deepcopy(binding))
    if not is_draft and (
        binding["strategy"]["strategy_id"] != strategy["strategy_id"]
        or not strategy_version_accepts(
            binding["strategy"]["strategy_version"], strategy["strategy_version"]
        )
    ):
        raise PlantingDocumentError(
            "binding requires the same strategy and a compatible forward version"
        )
    validate_profile_parameters(strategy, binding)
    roles = {item["role_key"]: item for item in strategy["roles"]}
    assignments = binding["assignments"]
    unknown = sorted(set(assignments) - set(roles))
    if unknown:
        raise PlantingDocumentError(
            "binding contains unknown roles: " + ", ".join(unknown)
        )
    missing = sorted(
        role_key
        for role_key, role in roles.items()
        if role["required"] and role_key not in assignments
    )
    if missing:
        raise PlantingDocumentError(
            "required roles are not mapped: " + ", ".join(missing)
        )
    for role_key, assignment in assignments.items():
        if not role_enabled(assignment):
            continue
        if (
            roles[role_key]["assignment_policy"] == "single"
            and len(assignment["candidates"]) != 1
        ):
            raise PlantingDocumentError(
                f"role {role_key!r} requires exactly one candidate"
            )


def _candidate_inputs(
    role_key: str,
    assignment: dict[str, Any],
    collections: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    if "collection_id" not in assignment:
        candidates = assignment.get("candidates")
        if not isinstance(candidates, list) or (role_enabled(assignment) and not candidates):
            raise PlantingDocumentError(
                f"assignments.{role_key}.candidates must be non-empty"
            )
        return candidates
    collection_id = assignment["collection_id"]
    collection = collections.get(collection_id)
    if collection is None:
        raise PlantingDocumentError(
            f"assignments.{role_key} references unknown collection {collection_id!r}"
        )
    return [
        {"asset_id": asset_id, "weight": 1.0}
        for asset_id in collection["asset_ids"]
    ]


def _copy_candidate(
    role_key: str,
    raw: dict[str, Any],
    assignment: dict[str, Any],
    catalog: dict[str, Any],
    assets: dict[str, dict[str, Any]],
    base_candidates: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    asset_id = raw.get("asset_id")
    base = base_candidates.get((role_key, asset_id))
    if base is not None:
        candidate = copy.deepcopy(base)
        candidate["weight"] = float(raw.get("weight", base["weight"]))
        candidate["scale"] = copy.deepcopy(
            raw.get("scale")
            or assignment.get("scale")
            or base["scale"]
        )
        return candidate
    asset = assets.get(asset_id)
    if asset is None:
        raise PlantingDocumentError(
            f"assignments.{role_key} references unknown asset {asset_id!r}"
        )
    target_hash, metadata_hash = catalog_asset_hashes(catalog, asset)
    candidate = {
        "asset_id": asset_id,
        "display_name_snapshot": asset["display_name"],
        "target_asset_ref": {
            "target_type": catalog["target_type"],
            "provider_id": catalog["provider_id"],
            "locator": copy.deepcopy(asset["provider_metadata"]),
        },
        "catalog_link": {
            "catalog_id": catalog["catalog_id"],
            "asset_id": asset_id,
            "observed_target_hash": target_hash,
            "observed_metadata_hash": metadata_hash,
        },
        "weight": float(raw.get("weight", 1.0)),
        "scale": copy.deepcopy(
            raw.get("scale")
            or assignment.get("scale")
            or asset.get("suggested_scale")
            or DEFAULT_SCALE
        ),
    }
    for field in ("reference_radius_m", "reference_height_m"):
        if field in asset:
            candidate[field] = float(asset[field])
    return candidate


def _base_candidates(
    base_binding: dict[str, Any] | None,
) -> dict[tuple[str, str], dict[str, Any]]:
    if base_binding is None:
        return {}
    binding = validate_profile(copy.deepcopy(base_binding))
    return {
        (role_key, candidate["asset_id"]): candidate
        for role_key, assignment in binding["assignments"].items()
        for candidate in assignment["candidates"]
    }
