"""Reviewed discovery metadata, separate from exchange-package and execution contracts."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any

from verdflow.contracts import stable_strategy_version, strategy_version_accepts
from verdflow.profile_package import ProfilePackageInspection
from verdflow.planting import prepare_role_binding_profile_document

from .resource_downloads import (
    MANIFEST_V2_URL, RESOURCE_INDEX_URL, StarterResource, load_resource_document, parse_resource,
)


@dataclass(frozen=True, order=True)
class CatalogSource:
    target_type: str
    catalog_id: str


@dataclass(frozen=True)
class PlanMetadata:
    profile_id: str
    semantic_hash: str
    strategy_id: str
    strategy_version: str
    strategy_hash: str
    catalogs: tuple[CatalogSource, ...] = ()


@dataclass(frozen=True)
class IndexedResource:
    resource: StarterResource
    strategy_hash: str = ""
    target_type: str = ""
    plan: PlanMetadata | None = None


@dataclass(frozen=True)
class ResourceIndex:
    revision: str
    resources: tuple[IndexedResource, ...]


def plan_metadata(inspection: ProfilePackageInspection) -> PlanMetadata:
    value = json.loads(inspection.profile.canonical_json)
    if prepare_role_binding_profile_document(value).semantic_hash != inspection.profile.semantic_hash:
        raise ValueError("方案内容指纹与实际内容不符。")
    target = inspection.profile.target_type
    ids = {candidate["catalog_link"]["catalog_id"]
           for assignment in value["assignments"].values()
           for candidate in assignment["candidates"] if candidate.get("catalog_link")}
    hint = value.get("catalog_hint", {}).get("catalog_id")
    if hint:
        ids.add(hint)
    return PlanMetadata(
        inspection.profile.profile_id, inspection.profile.semantic_hash,
        inspection.strategy.strategy_id, inspection.strategy.strategy_version,
        inspection.strategy.package_hash, tuple(CatalogSource(target, id) for id in sorted(ids)),
    )


def _text(value: Any) -> str:
    if not isinstance(value, str) or not 0 < len(value) <= 1024:
        raise ValueError("Invalid resource metadata")
    return value


def _hash(value: Any) -> str:
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", _text(value)):
        raise ValueError("Invalid content fingerprint")
    return value


def parse_index(payload: bytes, *, starter: bool = False) -> ResourceIndex:
    value = json.loads(payload)
    if not isinstance(value, dict) or value.get("schemaVersion") != (2 if starter else 1):
        raise ValueError("Unsupported resource discovery document")
    revision = value.get("revision")
    if not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{64}", revision):
        raise ValueError("Invalid resource revision")
    records = value.get("resources")
    if not isinstance(records, list) or len(records) > (32 if starter else 1024):
        raise ValueError("Invalid resource index size")
    result: list[IndexedResource] = []
    identities: set[tuple[str, str, str]] = set()
    owners: dict[tuple[str, str, str], str] = {}
    selected_ids: set[str] = set()
    for raw in records:
        if not isinstance(raw, dict):
            raise ValueError("Invalid resource index entry")
        if raw.get("status") == "withdrawn":
            continue
        if raw.get("status") != "published":
            raise ValueError("Invalid resource publication state")
        kind = raw.get("kind")
        if kind not in {"assets", "strategies", "plans"}:
            raise ValueError("Unsupported resource kind")
        fields = ("id", "name", "kind", "version", "identity", "filename", "bytes", "sha256", "url", "page_url")
        item = parse_resource({key: raw.get(key) for key in fields}, allow_plans=True)
        if starter and item.id in selected_ids:
            raise ValueError("Duplicate starter resource")
        selected_ids.add(item.id)
        plan = None
        strategy_hash = _hash(raw.get("strategy_hash")) if kind == "strategies" else ""
        target = _text(raw.get("target_type")) if kind == "assets" else ""
        if kind == "plans":
            meta = raw.get("plan")
            if not isinstance(meta, dict) or not isinstance(meta.get("catalogs"), list) or len(meta["catalogs"]) > 128:
                raise ValueError("Missing inspected plan metadata")
            if meta.get("profile_id") != item.identity:
                raise ValueError("Plan metadata identity mismatch")
            catalogs = tuple(sorted({CatalogSource(_text(c.get("target_type")), _text(c.get("catalog_id")))
                                     for c in meta["catalogs"] if isinstance(c, dict)}))
            if len(catalogs) != len(meta["catalogs"]):
                raise ValueError("Invalid or duplicate catalog sources")
            plan = PlanMetadata(item.identity, _hash(meta.get("semantic_hash")), _text(meta.get("strategy_id")),
                                _text(meta.get("strategy_version")), _hash(meta.get("strategy_hash")), catalogs)
        key = (kind, target + "/" + item.identity, item.version)
        if key in identities:
            raise ValueError("资源身份或版本有歧义，不能自动选择。")
        identities.add(key)
        owner_key = (kind, target, item.identity)
        if owner_key in owners and owners[owner_key] != item.id:
            raise ValueError("资源身份的发布归属存在歧义。")
        owners[owner_key] = item.id
        result.append(IndexedResource(item, strategy_hash, target, plan))
    return ResourceIndex(revision, tuple(result))


def load_index(*, starter: bool = False) -> ResourceIndex:
    return parse_index(load_resource_document(MANIFEST_V2_URL if starter else RESOURCE_INDEX_URL), starter=starter)


def accepts_dependency(meta: PlanMetadata, identity: str, version: str, fingerprint: str) -> bool:
    return (meta.strategy_id == identity and strategy_version_accepts(meta.strategy_version, version)
            and (version != meta.strategy_version or fingerprint == meta.strategy_hash))


def choose_strategy(meta: PlanMetadata, resources: tuple[IndexedResource, ...]) -> IndexedResource | None:
    candidates = [item for item in resources if item.resource.kind == "strategies"
                  and accepts_dependency(meta, item.resource.identity, item.resource.version, item.strategy_hash)]
    candidates.sort(key=lambda item: stable_strategy_version(item.resource.version) or (0, 0, 0), reverse=True)
    return candidates[0] if candidates else None
