"""Resolve reusable plans without mutating their stored dependency or trust."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from verdflow.contracts import LibraryStrategyInfo, StrategyTrustState
from verdflow.contracts import (
    stable_strategy_version,
    strategy_version_accepts,
)
from verdflow.library_store import LibraryStore
from verdflow.planting import (
    PlantingDocumentError, validate_binding_strategy, complete_profile_assignments,
    profile_generation_args, validate_profile_parameters,
)
from verdflow.strategy_worker import inspect_strategy_package


@dataclass(frozen=True, slots=True)
class ProfileStrategyResolution:
    strategy: LibraryStrategyInfo | None
    issue: str | None = None


class ProfileStrategyResolver:
    """Request-scoped package cache shared across a library listing."""

    def __init__(self, store: LibraryStore) -> None:
        self.store = store
        self.strategies = store.list_strategies()
        self._documents: dict[str, dict[str, Any]] = {}
        self._profiles: dict[str, dict[str, Any]] = {}
        self._trusted: dict[str, bool] = {}

    def document(self, package: LibraryStrategyInfo) -> dict[str, Any]:
        if package.package_hash not in self._documents:
            inspected = inspect_strategy_package(
                self.store.strategy_package_root(package.package_hash)
            )
            if inspected.package_hash != package.package_hash:
                raise ValueError("Strategy package hash changed after installation")
            self._documents[package.package_hash] = inspected.strategy
        return self._documents[package.package_hash]

    def trusted(self, package: LibraryStrategyInfo) -> bool:
        if package.package_hash not in self._trusted:
            decision = self.store.get_strategy_trust(package.package_hash)
            self._trusted[package.package_hash] = bool(
                decision and decision.state is StrategyTrustState.TRUSTED
            )
        return self._trusted[package.package_hash]

    def accepts(self, package: LibraryStrategyInfo, profile: dict[str, Any]) -> bool:
        reference = profile["strategy"]
        if package.package_id != reference["strategy_id"] or not strategy_version_accepts(
            reference["strategy_version"], package.package_version
        ):
            return False
        try:
            validate_binding_strategy(self.document(package), profile)
        except PlantingDocumentError:
            return False
        return True

    def resolve(
        self,
        profile: dict[str, Any],
        *,
        require_trust: bool = True,
        original_package_hash: str | None = None,
    ) -> ProfileStrategyResolution:
        reference = profile["strategy"]
        candidates = [
            package for package in self.strategies
            if package.package_id == reference["strategy_id"]
            and strategy_version_accepts(reference["strategy_version"], package.package_version)
            and (not require_trust or self.trusted(package))
            and (
                original_package_hash is None
                or package.package_version != reference["strategy_version"]
                or package.package_hash == original_package_hash
            )
        ]
        candidates.sort(
            key=lambda package: stable_strategy_version(package.package_version) or (0, 0, 0),
            reverse=True,
        )
        issue = "没有已安装且已信任的兼容策略" if require_trust else "没有已安装的兼容策略"
        for package in candidates:
            try:
                validate_binding_strategy(self.document(package), profile)
            except PlantingDocumentError as error:
                issue = f"方案配置与可用策略不兼容：{error}"
                continue
            return ProfileStrategyResolution(package)
        return ProfileStrategyResolution(None, issue)

    def load_profile(self, profile_id: str) -> dict[str, Any]:
        if profile_id not in self._profiles:
            self._profiles[profile_id] = json.loads(
                self.store.load_role_binding_profile_document(profile_id).canonical_json
            )
        return self._profiles[profile_id]

    def prepare_execution(
        self, package_hash: str, profile_id: str,
        overrides: dict[str, Any], args: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        package = self.store.get_strategy_by_hash(package_hash)
        if package is None or not self.trusted(package):
            raise ValueError("Selected strategy is not installed and trusted")
        strategy = self.document(package)
        profile = self.load_profile(profile_id)
        assignments = complete_profile_assignments(strategy, profile)
        for key, value in overrides.items():
            assignments[key] = {**assignments.get(key, {}), **value}
        parameters = {
            item["key"]: item["default"] for item in strategy.get("generation_parameters", [])
        }
        frozen_args = profile_generation_args(profile, args)
        validate_profile_parameters(strategy, frozen_args)
        parameters.update(frozen_args.get("strategy_parameters", {}))
        frozen_args["strategy_parameters"] = parameters
        validate_binding_strategy(strategy, {
            **profile, "assignments": assignments, "strategy_parameters": parameters,
        })
        return assignments, frozen_args
