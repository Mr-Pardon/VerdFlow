"""Read-only, bounded multi-catalog browsing on the Library owner thread."""

from collections import OrderedDict
from dataclasses import dataclass, replace
import hashlib
import json
from typing import Any

from verdflow.library_store import LibraryStore
from verdflow.contracts import AssetCatalogInfo
from .library_views import _catalog_asset
from .models import AssetCatalogAssetInfo


@dataclass(frozen=True, slots=True)
class AssetSearchRequest:
    target_type: str
    provider_id: str
    catalog_ids: tuple[str, ...]
    query: str = ""
    collection_catalog_id: str | None = None
    collection_id: str | None = None
    offset: int = 0
    limit: int = 48
    revision: str | None = None

    def validate(self) -> None:
        if not isinstance(self.target_type, str) or not isinstance(self.provider_id, str) or not self.target_type.strip() or not self.provider_id.strip():
            raise ValueError("Asset search requires one target and Provider")
        if len(self.catalog_ids) > 128 or any(not isinstance(key, str) or not key.strip() for key in self.catalog_ids):
            raise ValueError("Invalid catalog scope")
        if type(self.offset) is not int or self.offset < 0 or type(self.limit) is not int or not 1 <= self.limit <= 200:
            raise ValueError("Invalid asset search page")
        if not isinstance(self.query, str) or len(self.query) > 256:
            raise ValueError("Asset search query is too long")
        if bool(self.collection_id) != bool(self.collection_catalog_id):
            raise ValueError("A collection must include its source catalog")
        if self.collection_catalog_id and self.collection_catalog_id not in self.catalog_ids:
            raise ValueError("Collection source is outside the search scope")


def parse_asset_search_request(document: dict[str, Any]) -> AssetSearchRequest:
    ids = document.get("catalog_ids")
    if not isinstance(ids, list) or any(not isinstance(key, str) for key in ids):
        raise ValueError("catalog_ids must be an array of strings")
    for key in ("target_type", "provider_id", "query"):
        if not isinstance(document.get(key, ""), str):
            raise ValueError(f"{key} must be text")
    for key in ("collection_catalog_id", "collection_id", "revision"):
        if document.get(key) is not None and not isinstance(document[key], str):
            raise ValueError(f"{key} must be text")
    request = AssetSearchRequest(
        target_type=document.get("target_type", ""), provider_id=document.get("provider_id", ""),
        catalog_ids=tuple(ids), query=document.get("query", "").strip(),
        collection_catalog_id=document.get("collection_catalog_id"), collection_id=document.get("collection_id"),
        offset=document.get("offset", 0), limit=document.get("limit", 48), revision=document.get("revision"),
    )
    request.validate()
    return request


@dataclass(frozen=True, slots=True)
class AssetSearchCollection:
    catalog_id: str
    source_catalog_name: str
    collection_id: str
    display_name: str
    asset_count: int


@dataclass(frozen=True, slots=True)
class AssetSearchIssue:
    catalog_id: str
    message: str


@dataclass(frozen=True, slots=True)
class AssetSearchPage:
    items: tuple[AssetCatalogAssetInfo, ...]
    collections: tuple[AssetSearchCollection, ...]
    total_count: int
    offset: int
    next_offset: int | None
    revision: str
    unavailable_catalogs: tuple[AssetSearchIssue, ...]


@dataclass(slots=True)
class _CatalogIndex:
    document: dict[str, Any]
    memberships: dict[str, tuple[str, ...]]
    searchable: tuple[str, ...]
    byte_size: int


class AssetSearchService:
    """Cache only immutable catalog documents, never assignments or execution inputs."""

    def __init__(self) -> None:
        self._cache: OrderedDict[tuple[str, str, str], _CatalogIndex] = OrderedDict()

    def clear(self) -> None:
        self._cache.clear()

    def _index(self, store: LibraryStore, info: AssetCatalogInfo) -> _CatalogIndex:
        key = (info.target_type, info.catalog_id, info.semantic_hash)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        raw = store.load_asset_catalog_document(info.target_type, info.catalog_id).canonical_json
        document = json.loads(raw)
        memberships: dict[str, tuple[str, ...]] = {}
        for collection in document.get("collections", []):
            for asset_id in collection["asset_ids"]:
                memberships[asset_id] = (*memberships.get(asset_id, ()), collection["collection_id"])
        index = _CatalogIndex(document, memberships, tuple(
            "\n".join((asset["asset_id"], asset["display_name"], *asset.get("tags", []))).casefold()
            for asset in document["assets"]
        ), len(raw))
        for stale in tuple(self._cache):
            if stale[:2] == key[:2]:
                del self._cache[stale]
        self._cache[key] = index
        while len(self._cache) > 16 or sum(item.byte_size for item in self._cache.values()) > 16 * 1024 * 1024:
            self._cache.popitem(last=False)
        return index

    def search(self, store: LibraryStore, request: AssetSearchRequest) -> AssetSearchPage:
        request.validate()
        indexes = []
        issues = []
        versions = []
        collections = []
        for catalog_id in sorted(set(request.catalog_ids)):
            info = store.get_asset_catalog(request.target_type, catalog_id)
            if info is None:
                issues.append(AssetSearchIssue(catalog_id, "资产库已移除或不可用"))
                versions.append((catalog_id, None))
                continue
            if info.provider_id != request.provider_id:
                raise ValueError("Search scope contains an incompatible Provider")
            try:
                index = self._index(store, info)
            except (ValueError, OSError, RuntimeError):
                issues.append(AssetSearchIssue(catalog_id, "资产库暂时无法读取"))
                versions.append((catalog_id, None))
                continue
            indexes.append((info, index))
            versions.append((catalog_id, info.semantic_hash, info.generation))
            collections.extend(AssetSearchCollection(
                catalog_id, info.display_name, item["collection_id"], item["display_name"], len(item["asset_ids"])
            ) for item in index.document.get("collections", []))

        revision = hashlib.sha256(json.dumps([
            request.target_type, request.provider_id, versions, request.query.casefold(),
            request.collection_catalog_id, request.collection_id,
        ], ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        offset = request.offset if not request.revision or request.revision == revision else 0
        matching = []
        query = request.query.casefold()
        for info, index in indexes:
            if request.collection_catalog_id and request.collection_catalog_id != info.catalog_id:
                continue
            for ordinal, asset in enumerate(index.document["assets"]):
                if request.collection_id and request.collection_id not in index.memberships.get(asset["asset_id"], ()):
                    continue
                if query and query not in index.searchable[ordinal]:
                    continue
                matching.append((index, ordinal, asset))
        if offset >= len(matching):
            offset = max(0, ((len(matching) - 1) // request.limit) * request.limit)
        items = []
        for index, ordinal, asset in matching[offset:offset + request.limit]:
            item = _catalog_asset(ordinal, asset, index.memberships, index.document)
            items.append(replace(item, asset_id=item.binding_candidate["asset_id"]))
        next_offset = offset + len(items) if offset + len(items) < len(matching) else None
        return AssetSearchPage(tuple(items), tuple(collections), len(matching), offset, next_offset, revision, tuple(issues))
