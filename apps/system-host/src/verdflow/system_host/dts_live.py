"""Resolve one immutable DTS Live execution choice from the user library."""

from __future__ import annotations

import json
from typing import Any

from verdflow.contracts import StrategyTrustState
from verdflow.contracts import strategy_version_accepts
from verdflow.dts_adapter import DtsLiveExecutionSnapshot
from verdflow.library_store import LibraryStore
from verdflow.planting import (
    build_effective_binding_from_draft,
    build_effective_binding_from_profile,
    build_effective_binding_snapshot,
    profile_generation_args,
)
from verdflow.strategy_worker import inspect_strategy_package

from .models import DtsLiveConfigurationRequest
from .profile_strategies import ProfileStrategyResolver


def freeze_dts_live_execution(
    store: LibraryStore,
    request: DtsLiveConfigurationRequest,
) -> DtsLiveExecutionSnapshot:
    """Freeze one trusted strategy and self-contained target binding."""

    strategy_package_hash = _required_text(
        request.strategy_package_hash,
        "strategy_package_hash",
    )
    strategy_info = store.get_strategy_by_hash(strategy_package_hash)
    if strategy_info is None:
        raise ValueError("Exact DTS Live strategy package is not installed")
    trust = store.get_strategy_trust(strategy_package_hash)
    if trust is None or trust.state is not StrategyTrustState.TRUSTED:
        raise ValueError("DTS Live strategy package is not trusted")
    package = inspect_strategy_package(
        store.strategy_package_root(strategy_package_hash)
    )
    if package.package_hash != strategy_package_hash:
        raise ValueError("DTS Live strategy package hash changed after installation")
    if (
        package.package_id != strategy_info.package_id
        or package.package_version != strategy_info.package_version
    ):
        raise ValueError("DTS Live strategy package metadata is inconsistent")

    if request.profile_id is not None:
        if request.catalog_id is not None:
            raise ValueError("Profile execution cannot select a separate Catalog")
        profile_id = _required_text(request.profile_id, "profile_id")
        profile_info = store.get_role_binding_profile(profile_id)
        if profile_info is None:
            raise ValueError("DTS Live Profile is not installed")
        profile_document = store.load_role_binding_profile_document(profile_id)
        if profile_document.semantic_hash != profile_info.semantic_hash:
            raise ValueError("DTS Live Profile document differs from its metadata")
        if profile_info.target_type != "dts":
            raise ValueError("DTS Live Profile must target dts")
        if (
            profile_info.strategy_id != package.package_id
            or not strategy_version_accepts(profile_info.strategy_version, package.package_version)
        ):
            raise ValueError("DTS Live Profile does not match the selected strategy")
        profile = _json_object(profile_document.canonical_json, "Profile")
        profile_semantic_hash = profile_info.semantic_hash
        binding_overrides, prepared_args = ProfileStrategyResolver(store).prepare_execution(
            package.package_hash, profile_id,
            _json_copy(request.assignments, "assignments"),
            _json_copy(request.generation_args, "generation_args"),
        )
        effective = build_effective_binding_from_profile(
            package.strategy,
            profile,
            overrides=binding_overrides,
        )
        asset_source_kind = "profile"
        asset_source_id = profile_id
        asset_source_generation = None
        asset_source_hash = profile_info.semantic_hash
    elif request.catalog_id is None:
        profile = None
        profile_id = None
        profile_semantic_hash = None
        binding_overrides = _json_copy(request.assignments, "assignments")
        effective = build_effective_binding_from_draft(
            package.strategy, binding_overrides, target_type="dts",
        )
        asset_source_kind = "draft"
        asset_source_hash = effective["asset_use"]["source"]["semantic_hash"]
        asset_source_id = asset_source_hash
        asset_source_generation = None
    else:
        profile = None
        profile_id = None
        profile_semantic_hash = None
        catalog_id = _required_text(request.catalog_id, "catalog_id")
        if not isinstance(request.assignments, dict) or not request.assignments:
            raise ValueError("Temporary DTS Live role mappings are required")
        binding_overrides = request.assignments
        catalog_info = store.get_asset_catalog("dts", catalog_id)
        if catalog_info is None:
            raise ValueError("DTS Live Catalog is not installed")
        catalog_document = store.load_asset_catalog_document("dts", catalog_id)
        if catalog_document.semantic_hash != catalog_info.semantic_hash:
            raise ValueError("DTS Live Catalog document differs from its metadata")
        catalog = _json_object(catalog_document.canonical_json, "Catalog")
        effective = build_effective_binding_snapshot(
            package.strategy,
            catalog,
            catalog_generation=catalog_info.generation,
            overrides=binding_overrides,
        )
        asset_source_kind = "catalog"
        asset_source_id = catalog_id
        asset_source_generation = catalog_info.generation
        asset_source_hash = catalog_info.semantic_hash
    snapshot = DtsLiveExecutionSnapshot(
        strategy_package_hash=package.package_hash,
        strategy_id=package.package_id,
        strategy_version=package.package_version,
        target_type="dts",
        asset_source_kind=asset_source_kind,
        asset_source_id=asset_source_id,
        asset_source_generation=asset_source_generation,
        asset_source_hash=asset_source_hash,
        effective_binding_semantic_hash=effective["semantic_hash"],
        profile_id=profile_id,
        profile_semantic_hash=profile_semantic_hash,
        binding_overrides=(binding_overrides or None),
        random_seed=request.random_seed,
        generation_args=prepared_args if profile_id else profile_generation_args(
            profile, _json_copy(request.generation_args, "generation_args")
        ),
    )
    snapshot.validate()
    return snapshot


def _json_copy(value: dict[str, Any], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"DTS Live {label} must be an object")
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError) as error:
        raise ValueError(f"DTS Live {label} must contain valid JSON values") from error


def _json_object(payload: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"DTS Live {label} is not valid JSON") from error
    if not isinstance(value, dict):
        raise ValueError(f"DTS Live {label} must be an object")
    return value


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    if value != value.strip():
        raise ValueError(f"{field} cannot contain outer whitespace")
    return value
