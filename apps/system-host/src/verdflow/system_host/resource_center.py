"""Read-only resource discovery. Display metadata never authorizes an import."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, replace
import json
import re
from threading import RLock
from time import monotonic
from typing import Callable
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from verdflow.contracts import stable_strategy_version

from .resource_index import IndexedResource, ResourceIndex, load_index

BASE_URL = "https://mr-pardon.github.io/verdflow-resources/"
_ID = r"[a-z0-9][a-z0-9.-]{0,99}"


def content_url(path: str) -> str:
    if not isinstance(path, str) or not re.fullmatch(
        rf"data/{_ID}/[a-f0-9]{{20}}/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_.-]+\.(?:json|md|png|jpg|jpeg|webp)", path
    ) or any(part in {".", ".."} for part in path.split("/")):
        raise ValueError("资源说明地址无效，请刷新资源中心。")
    return BASE_URL + path


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("资源说明地址发生跳转，已停止读取。")


def load_content(path: str) -> bytes:
    url = BASE_URL + path if path == "catalog.json" else content_url(path)
    limit = 256_000 if path.endswith(".md") else 2 * 1024 * 1024
    request = Request(url, headers={"User-Agent": "VerdFlow", "Cache-Control": "no-cache"})
    with build_opener(_NoRedirect()).open(request, timeout=10) as response:
        result = response.read(limit + 1)
    if len(result) > limit:
        raise ValueError("资源说明超出大小限制。")
    return result


def _text(value: object, limit: int = 4000) -> str:
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError("资源说明格式无效。")
    return value


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > 32:
        raise ValueError("资源标签格式无效。")
    return tuple(_text(v, 100) for v in value)


@dataclass(frozen=True)
class InstalledStrategy:
    identity: str
    version: str
    fingerprint: str


@dataclass(frozen=True)
class ResourceInventory:
    strategies: tuple[InstalledStrategy, ...]
    catalogs: tuple[tuple[str, str], ...]
    plans: tuple[tuple[str, str], ...]


def local_state(item: IndexedResource, inventory: ResourceInventory) -> str:
    r = item.resource
    if r.kind == "strategies":
        versions = [s for s in inventory.strategies if s.identity == r.identity and s.version == r.version]
        return "installed" if any(s.fingerprint == item.strategy_hash for s in versions) else "conflict" if versions else "available"
    if r.kind == "plans":
        versions = [digest for identity, digest in inventory.plans if identity == r.identity]
        return "installed" if item.plan and item.plan.semantic_hash in versions else "conflict" if versions else "available"
    # Catalog IDs do not carry a distribution version: never claim exact content equality.
    return "local" if (item.target_type, r.identity) in inventory.catalogs else "available"


@dataclass(frozen=True)
class CenterResource:
    id: str
    kind: str
    name: str
    summary: str
    author: str
    platforms: tuple[str, ...]
    tags: tuple[str, ...]
    version: str
    updated_at: str
    cover: str
    detail: str
    local_state: str = "unavailable"


def parse_catalog(payload: bytes) -> tuple[CenterResource, ...]:
    value = json.loads(payload)
    if not isinstance(value, dict) or value.get("schemaVersion") != 1:
        raise ValueError("资源目录版本暂不支持。")
    records = value.get("resources")
    if not isinstance(records, list) or len(records) > 1024:
        raise ValueError("资源目录超出限制。")
    result = []
    seen = set()
    for r in records:
        if not isinstance(r, dict):
            raise ValueError("资源目录条目格式无效。")
        identity = _text(r.get("id"), 100)
        if not re.fullmatch(_ID, identity) or identity in seen or r.get("type") not in {"assets", "strategies", "plans"}:
            raise ValueError("资源目录身份重复或无效。")
        seen.add(identity)
        detail = _text(r.get("detail"))
        content_url(detail)
        if not detail.startswith(f"data/{identity}/") or not detail.endswith("/resource.json"):
            raise ValueError("资源目录与详情身份不一致。")
        if r.get("status") != "published":
            continue
        cover = content_url(r["cover"]) if r.get("cover") else ""
        if cover and (not cover.startswith(BASE_URL + detail.rsplit("/", 1)[0] + "/") or not re.search(r"\.(png|jpe?g|webp)$", cover)):
            raise ValueError("资源封面地址无效。")
        result.append(CenterResource(identity, r["type"], _text(r.get("name"), 200), _text(r.get("summary")),
            _text(r.get("author"), 200), _strings(r.get("platforms", [])), _strings(r.get("tags", [])),
            _text(r.get("version"), 100), _text(r.get("updatedAt"), 100), cover, detail))
    return tuple(result)


@dataclass(frozen=True)
class CenterCatalog:
    resources: tuple[CenterResource, ...]
    revision: str
    stale: bool = False
    warning: str = ""


@dataclass(frozen=True)
class ResourceRelation:
    kind: str
    name: str
    resource_id: str
    version: str
    url: str
    note: str


@dataclass(frozen=True)
class CenterVersion:
    version: str
    released_at: str
    notes: str
    bytes: int
    installable: bool
    local_state: str
    relations: tuple[ResourceRelation, ...]


@dataclass(frozen=True)
class CenterDetail:
    resource: CenterResource
    license: str
    versions: tuple[CenterVersion, ...]
    selected_version: str
    readme: str
    content_base: str
    revision: str
    warning: str = ""


@dataclass(frozen=True)
class StrategyUpdate:
    strategy_id: str
    resource_id: str
    current_version: str
    version: str
    major_change: bool


@dataclass(frozen=True)
class StrategyUpdates:
    items: tuple[StrategyUpdate, ...]
    warning: str = ""


def find_strategy_updates(index: ResourceIndex, inventory: ResourceInventory) -> tuple[StrategyUpdate, ...]:
    result = []
    for identity in sorted({s.identity for s in inventory.strategies}):
        installed = [s for s in inventory.strategies if s.identity == identity and stable_strategy_version(s.version)]
        if not installed:
            continue
        current = max(installed, key=lambda s: stable_strategy_version(s.version))
        candidates = [r.resource for r in index.resources if r.resource.kind == "strategies"
                      and r.resource.identity == identity and stable_strategy_version(r.resource.version)
                      and stable_strategy_version(r.resource.version) > stable_strategy_version(current.version)]
        if candidates:
            latest = max(candidates, key=lambda r: stable_strategy_version(r.version))
            result.append(StrategyUpdate(identity, latest.id, current.version, latest.version,
                stable_strategy_version(current.version)[0] != stable_strategy_version(latest.version)[0]))
    return tuple(result)


class ResourceCenterService:
    def __init__(self, inventory: Callable[[], ResourceInventory]) -> None:
        self.inventory = inventory
        self._lock = RLock()
        self._catalog: tuple[CenterResource, ...] | None = None
        self._index: ResourceIndex | None = None
        self._catalog_time = self._index_time = 0.0
        self._documents: OrderedDict[str, bytes] = OrderedDict()

    def _load_index(self, refresh: bool) -> tuple[ResourceIndex, str]:
        with self._lock:
            if refresh or self._index is None or monotonic() - self._index_time > 300:
                try:
                    self._index = load_index()
                    self._index_time = monotonic()
                except Exception:
                    return self._index or ResourceIndex("", ()), "暂时无法刷新导入清单；请检查网络后重试。"
            return self._index, ""

    def catalog(self, refresh: bool = False) -> CenterCatalog:
        warning = ""
        with self._lock:
            if refresh or self._catalog is None or monotonic() - self._catalog_time > 300:
                try:
                    self._catalog = parse_catalog(load_content("catalog.json"))
                    self._catalog_time = monotonic()
                except Exception as error:
                    if self._catalog is None:
                        raise ValueError("暂时无法读取资源中心，请检查网络后重试。本地资源仍可正常使用。") from error
                    warning = "暂时无法刷新资源中心，正在显示本次已加载的内容。"
            records = self._catalog
        index, index_warning = self._load_index(refresh)
        inventory = self.inventory()
        resources = []
        for r in records:
            item = next((i for i in index.resources if (i.resource.id, i.resource.version, i.resource.kind) == (r.id, r.version, r.kind)), None)
            resources.append(replace(r, local_state=local_state(item, inventory) if item else "unavailable"))
        return CenterCatalog(tuple(resources), index.revision, bool(warning or index_warning), warning or index_warning)

    def updates(self, refresh: bool = False) -> StrategyUpdates:
        index, warning = self._load_index(refresh)
        return StrategyUpdates(find_strategy_updates(index, self.inventory()), warning)

    def _document(self, path: str) -> bytes:
        with self._lock:
            if path not in self._documents:
                try:
                    self._documents[path] = load_content(path)
                except Exception as error:
                    raise ValueError("资源详情读取失败，请检查网络后重试。") from error
                while len(self._documents) > 24:
                    self._documents.popitem(last=False)
            self._documents.move_to_end(path)
            return self._documents[path]

    def detail(self, identity: str, version: str = "") -> CenterDetail:
        catalog = self.catalog()
        resource = next((r for r in catalog.resources if r.id == identity), None)
        if resource is None:
            raise ValueError("该资源已下架或不存在，请刷新资源中心。")
        value = json.loads(self._document(resource.detail))
        if not isinstance(value, dict) or value.get("schemaVersion") != 1 or value.get("id") != identity or value.get("type") != resource.kind:
            raise ValueError("资源说明与目录不一致，请刷新后重试。")
        index, warning = self._load_index(False)
        inventory = self.inventory()
        records = value.get("versions")
        if not isinstance(records, list) or len(records) > 256:
            raise ValueError("资源版本列表无效。")
        versions = []
        selected = version or resource.version
        readme = ""
        base = resource.detail.rsplit("/", 1)[0] + "/"
        content_base = BASE_URL + base
        seen_versions: set[str] = set()
        for r in records:
            if not isinstance(r, dict):
                raise ValueError("资源版本条目格式无效。")
            if r.get("status") != "published":
                continue
            v = _text(r.get("version"), 100)
            if v in seen_versions:
                raise ValueError("资源版本重复，请刷新资源中心。")
            seen_versions.add(v)
            item = next((i for i in index.resources if (i.resource.id, i.resource.version, i.resource.kind) == (identity, v, resource.kind)), None)
            package = r.get("package", {})
            if not isinstance(package, dict):
                raise ValueError("资源包说明格式无效。")
            matches = bool(item and (package.get("identity"), package.get("sha256"), package.get("bytes")) ==
                           (item.resource.identity, item.resource.sha256, item.resource.bytes))
            relations = []
            raw_relations = r.get("relations", [])
            if not isinstance(raw_relations, list) or len(raw_relations) > 128:
                raise ValueError("关联资源列表无效。")
            for relation in raw_relations:
                if not isinstance(relation, dict):
                    raise ValueError("关联资源条目格式无效。")
                url = _text(relation.get("url", ""))
                if url and (urlsplit(url).scheme != "https" or not urlsplit(url).hostname or urlsplit(url).username or urlsplit(url).password):
                    url = ""
                relations.append(ResourceRelation(_text(relation.get("kind", "")), _text(relation.get("name", "")),
                    _text(relation.get("resourceId", "")), _text(relation.get("version", "")), url, _text(relation.get("note", ""))))
            versions.append(CenterVersion(v, _text(r.get("releasedAt", "")), _text(r.get("notes", "")),
                item.resource.bytes if matches else 0, matches, local_state(item, inventory) if matches else "unavailable", tuple(relations)))
            if v == selected:
                path = base + _text(r.get("readme", "README.md"))
                if not path.endswith(".md"):
                    raise ValueError("资源说明类型无效。")
                content_url(path)
                readme = self._document(path).decode("utf-8")
                content_base = BASE_URL + path.rsplit("/", 1)[0] + "/"
        if selected not in {v.version for v in versions}:
            raise ValueError("所选版本已下架或不存在，请选择其他版本。")
        return CenterDetail(resource, _text(value.get("license", "")), tuple(versions), selected,
                            readme, content_base, index.revision, catalog.warning or warning)
