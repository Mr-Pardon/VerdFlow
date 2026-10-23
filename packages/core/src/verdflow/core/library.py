"""Read-only Core ports for user-scoped library facts."""

from __future__ import annotations

from typing import Any, Protocol

from verdflow.contracts import (
    AssetCatalogDocument,
    AssetCatalogInfo,
    LibraryStrategyInfo,
    RoleBindingProfileDocument,
    RoleBindingProfileInfo,
    StrategyTrustDecision,
    StrategyTrustState,
    require_package_hash,
)


class UserLibraryPort(Protocol):
    """Look up user-scoped installed resources without exposing storage."""

    def get_strategy_by_hash(
        self,
        package_hash: str,
    ) -> LibraryStrategyInfo | None: ...


class StrategyDocumentPort(Protocol):
    """Load one validated strategy without exposing package files to Core."""

    def load_strategy_document(
        self,
        package_hash: str,
        package_artifact_id: str,
    ) -> dict[str, Any]: ...


class StrategyTrustPort(Protocol):
    """Read current user trust; project artifacts never implement this port."""

    def get_strategy_trust(
        self,
        package_hash: str,
    ) -> StrategyTrustDecision | None: ...


class AssetCatalogLibraryPort(Protocol):
    """Load the current target Catalog; Core freezes selected facts on use."""

    def get_asset_catalog(
        self,
        target_type: str,
        catalog_id: str,
    ) -> AssetCatalogInfo | None: ...

    def load_asset_catalog_document(
        self,
        target_type: str,
        catalog_id: str,
    ) -> AssetCatalogDocument: ...


class RoleBindingProfileLibraryPort(Protocol):
    """Load one current self-contained Profile without exposing Library storage."""

    def get_role_binding_profile(
        self,
        profile_id: str,
    ) -> RoleBindingProfileInfo | None: ...

    def load_role_binding_profile_document(
        self,
        profile_id: str,
    ) -> RoleBindingProfileDocument: ...

__all__ = [
    "AssetCatalogLibraryPort",
    "LibraryStrategyInfo",
    "RoleBindingProfileLibraryPort",
    "StrategyTrustDecision",
    "StrategyDocumentPort",
    "StrategyTrustPort",
    "StrategyTrustState",
    "UserLibraryPort",
    "require_package_hash",
]
