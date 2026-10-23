"""Prepare validated canonical documents for Infrastructure persistence."""

from __future__ import annotations

import copy
from typing import Any

from verdflow.contracts import (
    AssetCatalogDocument,
    RoleBindingProfileDocument,
)

from .canonical import canonical_json_bytes, semantic_hash
from .validation import validate_catalog, validate_profile


def prepare_asset_catalog_document(
    value: dict[str, Any],
) -> AssetCatalogDocument:
    catalog = validate_catalog(copy.deepcopy(value))
    payload = canonical_json_bytes(catalog)
    return AssetCatalogDocument(
        target_type=catalog["target_type"],
        provider_id=catalog["provider_id"],
        catalog_id=catalog["catalog_id"],
        display_name=catalog.get("display_name") or catalog["catalog_id"],
        semantic_hash=semantic_hash(catalog),
        canonical_json=payload,
    )

def prepare_role_binding_profile_document(
    value: dict[str, Any],
) -> RoleBindingProfileDocument:
    profile = validate_profile(copy.deepcopy(value))
    payload = canonical_json_bytes(profile)
    return RoleBindingProfileDocument(
        profile_id=profile["profile_id"],
        display_name=profile["display_name"],
        strategy_id=profile["strategy"]["strategy_id"],
        strategy_version=profile["strategy"]["strategy_version"],
        target_type=profile["target"]["target_type"],
        provider_id=profile["target"]["provider_id"],
        catalog_id=(profile.get("catalog_hint") or {}).get("catalog_id"),
        semantic_hash=semantic_hash(profile),
        canonical_json=payload,
    )
