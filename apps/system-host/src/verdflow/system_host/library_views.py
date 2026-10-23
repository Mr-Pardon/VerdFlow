"""Presentation-neutral queries over user-scoped Library facts."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from verdflow.contracts import (
    AssetCatalogInfo,
    LibraryStrategyInfo,
    RoleBindingProfileInfo,
    StrategyTrustState,
    parse_library_resource_reference,
)
from verdflow.planting import (
    binding_catalog_drift,
    catalog_binding_candidate,
    PlantingDocumentError,
    strategy_preview_sample,
)
from verdflow.library_store import LibraryStore
from verdflow.strategy_worker import inspect_strategy_package

from .models import (
    AssetCatalogAssetInfo,
    AssetCatalogAssetPage,
    AssetCatalogCollectionInfo,
    AssetCatalogSummary,
    RoleBindingProfileDetail,
    RoleBindingProfileSummary,
    ProfilePlantPreview,
    StrategyLibraryDetail,
    StrategyLibrarySummary,
    StrategyGenerationParameterInfo,
    StrategyRoleInfo,
)
from .strategy_role_guides import present_strategy_roles
from .profile_strategies import ProfileStrategyResolver


def strategy_summary(
    store: LibraryStore,
    strategy: LibraryStrategyInfo,
) -> StrategyLibrarySummary:
    decision = store.get_strategy_trust(strategy.package_hash)
    state = decision.state.value if decision is not None else None
    return StrategyLibrarySummary(
        library_entry_id=strategy.library_entry_id,
        strategy_id=strategy.package_id,
        strategy_version=strategy.package_version,
        package_hash=strategy.package_hash,
        display_name=strategy.display_name,
        trust_state=state,
        executable=(
            decision is not None
            and decision.state is StrategyTrustState.TRUSTED
        ),
    )


def strategy_detail(
    store: LibraryStore,
    package_hash: str,
) -> StrategyLibraryDetail | None:
    strategy = store.get_strategy_by_hash(package_hash)
    if strategy is None:
        return None
    package = inspect_strategy_package(store.strategy_package_root(package_hash))
    if package.package_hash != package_hash:
        raise ValueError("Strategy package semantic hash changed after installation")
    document = package.strategy
    try:
        preview = strategy_preview_sample(document)
    except PlantingDocumentError:
        preview = None
    return StrategyLibraryDetail(
        summary=strategy_summary(store, strategy),
        description=_optional_text(document.get("description")),
        intent=_object(document.get("intent")),
        terrain_suitability=_object(document.get("terrain_suitability")),
        roles=present_strategy_roles(
            document, tuple(_strategy_role(item) for item in document["roles"]),
        ),
        default_random_seed=_strategy_default_seed(document),
        generation_parameters=tuple(
            _strategy_generation_parameter(item)
            for item in document.get("generation_parameters", [])
        ),
        parameter_preview=preview,
    )


def catalog_summary(
    catalog: AssetCatalogInfo,
) -> AssetCatalogSummary:
    return _catalog_summary(catalog)


def catalog_asset_page(
    store: LibraryStore,
    catalog: AssetCatalogInfo,
    *,
    after_ordinal: int,
    limit: int,
    collection_id: str | None = None,
    query: str | None = None,
) -> AssetCatalogAssetPage:
    document = _load_json(
        store.load_asset_catalog_document(
            catalog.target_type,
            catalog.catalog_id,
        ).canonical_json
    )
    return _catalog_document_asset_page(
        _catalog_summary(catalog),
        document,
        after_ordinal=after_ordinal,
        limit=limit,
        collection_id=collection_id,
        query=query,
    )

def _catalog_document_asset_page(
    summary: AssetCatalogSummary,
    document: dict[str, Any],
    *,
    after_ordinal: int,
    limit: int,
    collection_id: str | None,
    query: str | None,
) -> AssetCatalogAssetPage:
    assets = _array(document.get("assets"), "Catalog assets")
    raw_collections = _array(
        document.get("collections", []),
        "Catalog collections",
    )
    collection_documents = tuple(_object(value) for value in raw_collections)
    collections = tuple(
        AssetCatalogCollectionInfo(
            collection_id=str(item["collection_id"]),
            display_name=str(item["display_name"]),
            asset_count=len(_array(item["asset_ids"], "Collection assets")),
        )
        for item in collection_documents
    )
    collection_members = {
        str(item["collection_id"]): {
            str(asset_id)
            for asset_id in _array(item["asset_ids"], "Collection assets")
        }
        for item in collection_documents
    }
    if collection_id is not None and collection_id not in collection_members:
        raise ValueError(f"Unknown Catalog collection: {collection_id}")
    memberships: dict[str, tuple[str, ...]] = {}
    for item in collection_documents:
        identifier = str(item["collection_id"])
        for asset_id in _array(item["asset_ids"], "Collection assets"):
            key = str(asset_id)
            memberships[key] = (*memberships.get(key, ()), identifier)

    normalized_query = query.casefold() if query is not None else None
    matching: list[tuple[int, object]] = []
    for ordinal, value in enumerate(assets):
        asset = _object(value)
        asset_id = str(asset["asset_id"])
        if (
            collection_id is not None
            and asset_id not in collection_members[collection_id]
        ):
            continue
        if normalized_query is not None:
            searchable = (
                asset_id,
                str(asset["display_name"]),
                *(
                    str(tag)
                    for tag in _array(
                        asset.get("tags", []),
                        "Catalog asset tags",
                    )
                ),
            )
            if not any(
                normalized_query in item.casefold()
                for item in searchable
            ):
                continue
        matching.append((ordinal, value))

    eligible = [item for item in matching if item[0] > after_ordinal]
    selected = eligible[:limit]
    items = tuple(
        _catalog_asset(ordinal, value, memberships, document)
        for ordinal, value in selected
    )
    next_after = (
        selected[-1][0]
        if selected and len(eligible) > len(selected)
        else None
    )
    return AssetCatalogAssetPage(
        catalog=summary,
        total_count=len(matching),
        collections=collections,
        items=items,
        next_after_ordinal=next_after,
    )


def profile_summary(
    profile: RoleBindingProfileInfo,
    store: LibraryStore | None = None,
    catalog_cache: dict[tuple[str, str], dict[str, Any] | None] | None = None,
    resolver: ProfileStrategyResolver | None = None,
) -> RoleBindingProfileSummary:
    if store is None:
        return _profile_summary(profile)
    resolver = resolver or ProfileStrategyResolver(store)
    document = resolver.load_profile(profile.profile_id)
    cache = catalog_cache if catalog_cache is not None else {}
    catalogs = profile_source_catalogs(store, document, cache)
    return _resolved_profile_summary(
        _profile_summary(profile, document, _profile_assets(document, catalogs)),
        document, resolver,
    )


def _resolved_profile_summary(
    summary: RoleBindingProfileSummary,
    document: dict[str, Any],
    resolver: ProfileStrategyResolver,
) -> RoleBindingProfileSummary:
    resolution = resolver.resolve(document)
    package = resolution.strategy
    return replace(
        summary,
        resolved_strategy_version=package.package_version if package else None,
        resolved_strategy_hash=package.package_hash if package else None,
        strategy_resolution_issue=resolution.issue,
    )


def profile_source_catalogs(
    store: LibraryStore, document: dict[str, Any],
    cache: dict[tuple[str, str], dict[str, Any] | None] | None = None,
) -> list[dict[str, Any]]:
    cache = cache if cache is not None else {}
    target_type = document["target"]["target_type"]
    ids = {candidate["catalog_link"]["catalog_id"]
           for assignment in document["assignments"].values()
           for candidate in assignment["candidates"] if "catalog_link" in candidate}
    if document.get("catalog_hint"):
        ids.add(document["catalog_hint"]["catalog_id"])
    result = []
    for identifier in sorted(ids):
        key = (target_type, identifier)
        if key not in cache:
            cache[key] = (_load_json(store.load_asset_catalog_document(*key).canonical_json)
                          if store.get_asset_catalog(*key) is not None else None)
        if cache[key] is not None:
            result.append(cache[key])
    return result


def _profile_assets(document: dict[str, Any], catalogs: list[dict[str, Any]]) -> dict[str, AssetCatalogAssetInfo]:
    sources = {item["catalog_id"]: item for item in catalogs}
    source_assets = {item["catalog_id"]: {asset["asset_id"]: asset for asset in item["assets"]} for item in catalogs}
    result = {}
    for assignment in document["assignments"].values():
        for candidate in assignment["candidates"]:
            link = candidate.get("catalog_link") or {
                "catalog_id": document.get("catalog_hint", {}).get("catalog_id"),
                "asset_id": candidate["asset_id"],
            }
            catalog = sources.get(link.get("catalog_id"))
            source = source_assets.get(link.get("catalog_id"), {}).get(link.get("asset_id"), {})
            asset = {
                "asset_id": candidate["asset_id"],
                "display_name": candidate["display_name_snapshot"],
                "provider_metadata": candidate["target_asset_ref"]["locator"],
                **({"suggested_scale": source["suggested_scale"]} if source.get("suggested_scale") else {}),
                **({"thumbnail": source["thumbnail"]} if source.get("thumbnail") else {}),
                **{field: candidate[field] for field in ("reference_radius_m", "reference_height_m") if field in candidate},
            }
            result[candidate["asset_id"]] = replace(
                _catalog_asset(0, asset, {}), binding_candidate=candidate,
                source_catalog_name=(catalog.get("display_name", catalog["catalog_id"]) if catalog else link.get("catalog_id")),
            )
    return result


def profile_detail(
    store: LibraryStore,
    profile: RoleBindingProfileInfo,
) -> RoleBindingProfileDetail:
    document = _load_json(
        store.load_role_binding_profile_document(profile.profile_id).canonical_json
    )
    assignments = _object(document.get("assignments"))
    catalogs = profile_source_catalogs(store, document)
    assets = _profile_assets(document, catalogs)
    drift = binding_catalog_drift(document, catalogs)
    return RoleBindingProfileDetail(
        summary=_resolved_profile_summary(
            _profile_summary(profile, document, assets), document, ProfileStrategyResolver(store)
        ),
        role_count=len(assignments),
        assignments={key: _object(value) for key, value in assignments.items()},
        catalog_drift=drift,
        drift_count=sum(value != "current" for value in drift.values()),
        strategy_parameters=document.get("strategy_parameters"),
        assets=tuple(assets.values()),
    )


def _strategy_role(value: object) -> StrategyRoleInfo:
    item = _object(value)
    return StrategyRoleInfo(
        role_key=str(item["role_key"]),
        display_name=str(item["display_name"]),
        description=str(item["description"]),
        required=bool(item["required"]),
        assignment_policy=str(item["assignment_policy"]),
    )


def _strategy_generation_parameter(
    value: object,
) -> StrategyGenerationParameterInfo:
    item = _object(value)
    return StrategyGenerationParameterInfo(
        key=str(item["key"]),
        value_type=str(item["value_type"]),
        default=float(item["default"]),
        minimum=float(item["min"]),
        maximum=float(item["max"]),
        step=float(item["step"]),
        group=str(item["group"]),
        display_name=str(item["display_name"]),
        effect=str(item["effect"]),
        visible_for_roles=tuple(item.get("visible_for_roles", [])),
    )


def _strategy_default_seed(document: dict[str, Any]) -> int | None:
    parameters = document.get("parameters")
    if not isinstance(parameters, dict):
        return None
    value = parameters.get("default_seed")
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def _catalog_asset(
    ordinal: int,
    value: object,
    memberships: dict[str, tuple[str, ...]],
    catalog: dict[str, Any] | None = None,
) -> AssetCatalogAssetInfo:
    item = _object(value)
    tags = _array(item.get("tags", []), "Catalog asset tags")
    thumbnail = item.get("thumbnail")
    return AssetCatalogAssetInfo(
        binding_candidate=catalog_binding_candidate(catalog, item) if catalog else None,
        source_catalog_name=catalog.get("display_name", catalog["catalog_id"]) if catalog else None,
        ordinal=ordinal,
        asset_id=str(item["asset_id"]),
        display_name=str(item["display_name"]),
        thumbnail_resource_id=(
            parse_library_resource_reference(str(thumbnail))
            if thumbnail is not None
            else None
        ),
        lod=_object(item.get("lod")),
        provider_metadata=_object(item.get("provider_metadata")),
        suggested_scale=(
            _object(item["suggested_scale"])
            if "suggested_scale" in item
            else None
        ),
        tags=tuple(str(tag) for tag in tags),
        collection_ids=memberships.get(str(item["asset_id"]), ()),
        reference_radius_m=(
            float(item["reference_radius_m"])
            if "reference_radius_m" in item
            else None
        ),
        reference_height_m=(
            float(item["reference_height_m"])
            if "reference_height_m" in item
            else None
        ),
    )


def _catalog_summary(
    catalog: AssetCatalogInfo,
) -> AssetCatalogSummary:
    return AssetCatalogSummary(
        catalog_entry_id=catalog.catalog_entry_id,
        target_type=catalog.target_type,
        provider_id=catalog.provider_id,
        catalog_id=catalog.catalog_id,
        display_name=catalog.display_name,
        generation=catalog.generation,
        semantic_hash=catalog.semantic_hash,
        created_at=catalog.created_at,
        updated_at=catalog.updated_at,
        origin_package_hash=catalog.origin_package_hash,
    )


def _profile_summary(
    profile: RoleBindingProfileInfo,
    document: dict[str, Any] | None = None,
    catalog_assets: dict[str, AssetCatalogAssetInfo] | None = None,
) -> RoleBindingProfileSummary:
    assignments = (document or {}).get("assignments", {})
    enabled = [item for item in assignments.values() if item.get("enabled", True)]
    candidates: dict[str, dict[str, Any]] = {}
    for assignment in enabled:
        for item in assignment.get("candidates", []):
            identity = json.dumps(item.get("target_asset_ref", {"asset_id": item["asset_id"]}), sort_keys=True)
            candidates.setdefault(identity, item)
    assets = catalog_assets or {}
    plants = []
    for item in list(candidates.values())[:4]:
        asset = assets.get(item["asset_id"])
        plants.append(ProfilePlantPreview(
            asset_id=item["asset_id"],
            display_name=item.get("display_name_snapshot", item["asset_id"]),
            thumbnail_resource_id=asset.thumbnail_resource_id if asset else None,
        ))
    return RoleBindingProfileSummary(
        profile_entry_id=profile.profile_entry_id,
        profile_id=profile.profile_id,
        display_name=profile.display_name,
        strategy_id=profile.strategy_id,
        strategy_version=profile.strategy_version,
        target_type=profile.target_type,
        provider_id=profile.provider_id,
        catalog_id=profile.catalog_id,
        generation=profile.generation,
        semantic_hash=profile.semantic_hash,
        updated_at=profile.updated_at,
        enabled_role_count=len(enabled),
        asset_count=len(candidates),
        plants=tuple(plants),
        has_parameter_preset="strategy_parameters" in (document or {}),
    )


def _load_json(payload: bytes) -> dict[str, Any]:
    value = json.loads(payload)
    return _object(value)


def _object(value: object) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("Stored Library document contains an invalid object")
    return dict(value)


def _array(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    return list(value)


def _optional_text(value: object) -> str:
    return str(value) if value is not None else ""
