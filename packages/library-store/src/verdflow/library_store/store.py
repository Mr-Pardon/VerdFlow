"""Public user-scoped LibraryStore facade."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import shutil

from verdflow.contracts import (
    AssetCatalogDocument,
    AssetCatalogInfo,
    AssetCatalogRemovalInfo,
    LibraryResourceInfo,
    LibraryStrategyInfo,
    RoleBindingProfileDocument,
    RoleBindingProfileInfo,
    StrategyTrustDecision,
    StrategyTrustState,
    require_package_hash,
)

from .catalogs import AssetCatalogRepository
from .database import LibraryDatabase
from .errors import LibraryIntegrityError, StrategyNotInstalledError
from .layout import LibraryLayout
from .locking import LibraryKernelGuard
from .migrations import cleanup_completed_migration_backups, migrate_to_current
from .packages import ManagedStrategyPackages
from .profiles import RoleBindingProfileRepository
from .resources import ManagedLibraryResources
from .schema import initialize_schema
from .strategies import StrategyRepository
from .trust import StrategyTrustRepository


class LibraryStore:
    """Own user-level package and trust facts with short write transactions."""

    def __init__(self, root: Path) -> None:
        self._layout = LibraryLayout.from_root(root)
        self._database = LibraryDatabase(self._layout.database)
        self._packages = ManagedStrategyPackages(self._layout)
        self._strategies = StrategyRepository(self._database)
        self._trust = StrategyTrustRepository(self._database)
        self._catalogs = AssetCatalogRepository(self._database)
        self._profiles = RoleBindingProfileRepository(self._database)
        self._resources = ManagedLibraryResources(self._layout, self._database)

    @classmethod
    def open(cls, root: Path) -> LibraryStore:
        store = cls(root)
        store._layout.prepare()
        try:
            with LibraryKernelGuard(store._layout.initialization_guard):
                database_exists = store._layout.database.is_file()
                store._database.open()
                if database_exists:
                    migrate_to_current(
                        store._database,
                        store._layout.migration_backups,
                    )
                else:
                    initialize_schema(store._database)
                errors = store._database.integrity_errors()
                if errors:
                    raise LibraryIntegrityError(
                        "Library database failed integrity validation: "
                        + "; ".join(errors)
                    )
                cleanup_completed_migration_backups(
                    store._layout.migration_backups
                )
            with LibraryKernelGuard(store._layout.package_mutation_guard):
                store._packages.cleanup_staging()
            with LibraryKernelGuard(store._layout.resource_mutation_guard):
                store._resources.cleanup_staging()
        except Exception:
            store.close()
            raise
        return store

    def __enter__(self) -> LibraryStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def root(self) -> Path:
        return self._layout.root

    def close(self) -> None:
        self._database.close()

    def install_verified_strategy(
        self,
        source_root: Path,
        *,
        package_id: str,
        package_version: str,
        package_hash: str,
        display_name: str,
    ) -> LibraryStrategyInfo:
        """Install bytes already inspected by the Strategy Worker adapter."""
        require_package_hash(package_hash)
        with LibraryKernelGuard(self._layout.package_mutation_guard):
            staged, payload_hash = self._packages.stage_copy(source_root)
            try:
                destination = self._packages.commit(
                    staged,
                    package_hash,
                    payload_hash,
                )
                relative = destination.relative_to(self._layout.root).as_posix()
                return self._strategies.register(
                    package_id=package_id,
                    package_version=package_version,
                    package_hash=package_hash,
                    display_name=display_name,
                    payload_hash=payload_hash,
                    install_relpath=relative,
                    installed_at=self._now_text(),
                )
            finally:
                if staged.exists():
                    shutil.rmtree(staged, ignore_errors=True)

    def get_strategy_by_hash(
        self,
        package_hash: str,
    ) -> LibraryStrategyInfo | None:
        strategy = self._strategies.find(package_hash)
        if strategy is None:
            return None
        self.strategy_package_root(package_hash)
        return strategy

    def list_strategies(self) -> tuple[LibraryStrategyInfo, ...]:
        strategies = self._strategies.list()
        for strategy in strategies:
            self.strategy_package_root(strategy.package_hash)
        return strategies

    def uninstall_strategy(self, package_hash: str) -> LibraryStrategyInfo:
        require_package_hash(package_hash)
        try:
            relative, payload_hash = self._strategies.storage_facts(package_hash)
        except KeyError as error:
            raise StrategyNotInstalledError(package_hash) from error
        with LibraryKernelGuard(self._layout.package_mutation_guard):
            self._packages.verify(relative, payload_hash)
            removed = self._strategies.unregister(package_hash)
            try:
                self._packages.remove_unregistered(relative, payload_hash)
            except (LibraryIntegrityError, OSError):
                # Registration is authoritative; orphan byte cleanup can retry later.
                pass
            return removed

    def strategy_package_root(self, package_hash: str) -> Path:
        try:
            relative, payload_hash = self._strategies.storage_facts(package_hash)
        except KeyError as error:
            raise StrategyNotInstalledError(package_hash) from error
        return self._packages.verify(relative, payload_hash)

    def trust_strategy(
        self,
        package_hash: str,
        *,
        decided_by: str,
        reason: str | None = None,
    ) -> StrategyTrustDecision:
        return self._trust.decide(
            package_hash=package_hash,
            state=StrategyTrustState.TRUSTED,
            decided_at=self._now_text(),
            decided_by=decided_by,
            reason=reason,
        )

    def revoke_strategy(
        self,
        package_hash: str,
        *,
        decided_by: str,
        reason: str | None = None,
    ) -> StrategyTrustDecision:
        return self._trust.decide(
            package_hash=package_hash,
            state=StrategyTrustState.REVOKED,
            decided_at=self._now_text(),
            decided_by=decided_by,
            reason=reason,
        )

    def get_strategy_trust(
        self,
        package_hash: str,
    ) -> StrategyTrustDecision | None:
        return self._trust.get(package_hash)

    def trust_event_count(self, package_hash: str) -> int:
        return self._trust.event_count(package_hash)

    def save_asset_catalog(
        self,
        document: AssetCatalogDocument,
        *,
        expected_generation: int,
        origin_package_hash: str | None = None,
    ) -> AssetCatalogInfo:
        return self._catalogs.save(
            document,
            expected_generation=expected_generation,
            changed_at=self._now_text(),
            origin_package_hash=origin_package_hash,
        )

    def import_resource(
        self,
        source: Path,
        *,
        media_type: str,
    ) -> LibraryResourceInfo:
        with LibraryKernelGuard(self._layout.resource_mutation_guard):
            return self._resources.import_file(source, media_type=media_type)

    def import_resource_bytes(
        self,
        payload: bytes,
        *,
        media_type: str,
    ) -> LibraryResourceInfo:
        with LibraryKernelGuard(self._layout.resource_mutation_guard):
            return self._resources.import_bytes(payload, media_type=media_type)

    def get_resource(self, resource_id: str) -> LibraryResourceInfo:
        return self._resources.get(resource_id)

    def read_resource(
        self,
        resource_id: str,
    ) -> tuple[LibraryResourceInfo, bytes]:
        return self._resources.read_bytes(resource_id)

    def get_asset_catalog(
        self,
        target_type: str,
        catalog_id: str,
    ) -> AssetCatalogInfo | None:
        return self._catalogs.find(target_type, catalog_id)

    def list_asset_catalogs(
        self,
        *,
        target_type: str | None = None,
    ) -> tuple[AssetCatalogInfo, ...]:
        return self._catalogs.list(target_type)

    def load_asset_catalog_document(
        self,
        target_type: str,
        catalog_id: str,
    ) -> AssetCatalogDocument:
        return self._catalogs.load_document(target_type, catalog_id)

    def delete_asset_catalog(
        self,
        target_type: str,
        catalog_id: str,
    ) -> AssetCatalogRemovalInfo:
        affected_profiles = self._catalogs.delete(target_type, catalog_id)
        return AssetCatalogRemovalInfo(
            target_type=target_type,
            catalog_id=catalog_id,
            removed=True,
            affected_profiles=affected_profiles,
        )

    def save_role_binding_profile(
        self,
        document: RoleBindingProfileDocument,
        *,
        expected_generation: int,
    ) -> RoleBindingProfileInfo:
        return self._profiles.save(
            document,
            expected_generation=expected_generation,
            changed_at=self._now_text(),
        )

    def get_role_binding_profile(
        self,
        profile_id: str,
    ) -> RoleBindingProfileInfo | None:
        return self._profiles.find(profile_id)

    def list_role_binding_profiles(
        self,
        *,
        target_type: str | None = None,
        catalog_id: str | None = None,
        strategy_id: str | None = None,
        strategy_version: str | None = None,
    ) -> tuple[RoleBindingProfileInfo, ...]:
        return self._profiles.list(
            target_type=target_type,
            catalog_id=catalog_id,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
        )

    def load_role_binding_profile_document(
        self,
        profile_id: str,
    ) -> RoleBindingProfileDocument:
        return self._profiles.load_document(profile_id)

    def delete_role_binding_profile(self, profile_id: str) -> bool:
        return self._profiles.delete(profile_id)

    @staticmethod
    def _now_text() -> str:
        return datetime.now(UTC).isoformat()
