"""Owner-thread plan checks and commits; no network or implicit trust changes."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Callable

from verdflow.library_store import LibraryStore
from verdflow.contracts import stable_strategy_version
from verdflow.planting import prepare_role_binding_profile_document
from verdflow.profile_package import ProfilePackageInspection

from .profile_strategies import ProfileStrategyResolver
from .resource_index import CatalogSource, PlanMetadata, accepts_dependency
from .runtime import LibraryRuntime


@dataclass(frozen=True)
class PlanImportState:
    generation: int
    same_content: bool
    strategy_version: str
    missing_catalogs: tuple[CatalogSource, ...]


class PlanImportLibrary:
    def __init__(self, runtime: Callable[[], LibraryRuntime], timeout: float) -> None:
        self.runtime = runtime
        self.timeout = timeout

    def inspect(self, metadata: PlanMetadata, inspection: ProfilePackageInspection | None = None) -> PlanImportState:
        def read(store: LibraryStore) -> PlanImportState:
            existing = store.get_role_binding_profile(metadata.profile_id)
            if inspection:
                value = json.loads(inspection.profile.canonical_json)
                prepare_role_binding_profile_document(value)
                selected = ProfileStrategyResolver(store).resolve(
                    value, require_trust=False, original_package_hash=metadata.strategy_hash,
                ).strategy
                version = selected.package_version if selected else ""
            else:
                # Remote metadata is only a preflight hint. Actual VFP contracts are checked before installation.
                candidates = [item for item in store.list_strategies() if accepts_dependency(
                    metadata, item.package_id, item.package_version, item.package_hash)]
                candidates.sort(key=lambda item: stable_strategy_version(item.package_version) or (0, 0, 0), reverse=True)
                version = candidates[0].package_version if candidates else ""
            missing = tuple(c for c in metadata.catalogs if store.get_asset_catalog(c.target_type, c.catalog_id) is None)
            return PlanImportState(existing.generation if existing else 0,
                                   bool(existing and existing.semantic_hash == metadata.semantic_hash), version, missing)
        return self.runtime().call(read, timeout_seconds=self.timeout)

    def catalog_exists(self, target: str, identity: str) -> bool:
        return self.runtime().call(lambda store: store.get_asset_catalog(target, identity) is not None,
                                   timeout_seconds=self.timeout)

    def commit(self, inspection: ProfilePackageInspection, expected_generation: int) -> tuple[bool, str]:
        value = json.loads(inspection.profile.canonical_json)
        document = prepare_role_binding_profile_document(value)

        def save(store: LibraryStore) -> tuple[bool, str]:
            resolver = ProfileStrategyResolver(store)
            selected = resolver.resolve(value, require_trust=False,
                                        original_package_hash=inspection.strategy.package_hash).strategy
            if selected is None:
                raise ValueError("没有通过校验的兼容策略，请补齐策略后重新检查。")
            existing = store.get_role_binding_profile(document.profile_id)
            if (existing.generation if existing else 0) != expected_generation:
                raise ValueError("方案在确认后已改变，请重新检查并确认，未覆盖本地修改。")
            same = bool(existing and existing.semantic_hash == document.semantic_hash)
            store.save_role_binding_profile(document, expected_generation=expected_generation)
            trusted = resolver.resolve(value, require_trust=True,
                                       original_package_hash=inspection.strategy.package_hash).strategy
            return same, "" if trusted else "方案已导入，使用前请在策略库确认信任。"
        return self.runtime().call(save, timeout_seconds=self.timeout)
