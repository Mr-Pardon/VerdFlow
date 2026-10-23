"""Portable Profile package values with no persistence dependency."""

from __future__ import annotations

from dataclasses import dataclass

from verdflow.contracts import RoleBindingProfileDocument, require_package_hash


@dataclass(frozen=True, slots=True)
class ProfileStrategyDependency:
    strategy_id: str
    strategy_version: str
    package_hash: str

    def __post_init__(self) -> None:
        _require_text(self.strategy_id, "strategy_id")
        _require_text(self.strategy_version, "strategy_version")
        require_package_hash(self.package_hash)


@dataclass(frozen=True, slots=True)
class ProfilePackageInspection:
    source_path: str
    package_hash: str
    profile: RoleBindingProfileDocument
    strategy: ProfileStrategyDependency


@dataclass(frozen=True, slots=True)
class ProfilePackageArchiveInfo:
    destination_path: str
    package_hash: str
    byte_size: int


def _require_text(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
