from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "worker-runtime" / "src"),
    str(ROOT / "packages" / "library-store" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
    str(ROOT / "packages" / "core" / "src"),
    str(ROOT / "packages" / "planting" / "src"),
    str(ROOT / "adapters" / "strategy-worker" / "src"),
]

from verdflow.contracts import (  # noqa: E402
    StrategyTrustState,
    library_resource_reference,
)
from verdflow.core import (  # noqa: E402
    StrategyAccessError,
    StrategyExecutionAuthorizer,
)
from verdflow.library_store import (  # noqa: E402
    LibraryIntegrityError,
    LibraryMigrationError,
    LibrarySchemaError,
    LibraryStore,
    StrategyInstallConflictError,
    StrategyNotInstalledError,
)
from verdflow.strategy_worker import StrategyWorker, inspect_strategy_package  # noqa: E402


NATURAL_ROOT = ROOT / "strategy-packs" / "universal-natural-grove"
STREET_ROOT = ROOT / "strategy-packs" / "universal-street-tree"


def install(store: LibraryStore, source: Path, display_name: str):
    package = inspect_strategy_package(source)
    info = store.install_verified_strategy(
        package.root,
        package_id=package.package_id,
        package_version=package.package_version,
        package_hash=package.package_hash,
        display_name=display_name,
    )
    return package, info


class LibraryStoreTests(unittest.TestCase):
    def test_managed_resource_is_deduplicated_and_survives_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library_root = root / "library"
            image = root / "thumbnail.png"
            image.write_bytes(b"\x89PNG\r\n\x1a\nverdflow-thumbnail")

            with LibraryStore.open(library_root) as store:
                first = store.import_resource(image, media_type="image/png")
                second = store.import_resource(image, media_type="image/png")
                self.assertEqual(first, second)
                self.assertEqual(
                    library_resource_reference(first.resource_id),
                    f"verdflow-resource:{first.resource_id}",
                )

            with LibraryStore.open(library_root) as reopened:
                info, payload = reopened.read_resource(first.resource_id)
                self.assertEqual(info, first)
                self.assertEqual(payload, image.read_bytes())

    def test_uninstall_removes_registration_trust_and_managed_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "library"
            with LibraryStore.open(root) as store:
                package, info = install(store, NATURAL_ROOT, "通用自然聚落")
                store.trust_strategy(
                    package.package_hash,
                    decided_by="test-user",
                )
                managed = store.strategy_package_root(package.package_hash)

                removed = store.uninstall_strategy(package.package_hash)

                self.assertEqual(removed.library_entry_id, info.library_entry_id)
                self.assertFalse(managed.exists())
                self.assertIsNone(
                    store.get_strategy_by_hash(package.package_hash)
                )
                self.assertIsNone(store.get_strategy_trust(package.package_hash))
                with self.assertRaises(StrategyNotInstalledError):
                    store.uninstall_strategy(package.package_hash)

    def test_installs_managed_package_and_reopens_with_exact_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "library"
            with LibraryStore.open(root) as store:
                package, info = install(store, NATURAL_ROOT, "通用自然聚落")
                managed_root = store.strategy_package_root(package.package_hash)
                managed = inspect_strategy_package(managed_root)

                self.assertNotEqual(managed_root, package.root)
                self.assertTrue(managed_root.is_relative_to(root))
                self.assertEqual(managed.package_hash, package.package_hash)
                self.assertEqual(info.package_hash, package.package_hash)

            with LibraryStore.open(root) as reopened:
                found = reopened.get_strategy_by_hash(package.package_hash)
                self.assertIsNotNone(found)
                self.assertEqual(found.library_entry_id, info.library_entry_id)
                self.assertEqual(len(reopened.list_strategies()), 1)

    def test_unknown_trusted_and_revoked_states_drive_core_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with LibraryStore.open(Path(temporary) / "library") as store:
                package, _ = install(store, STREET_ROOT, "通用行道树")
                authorizer = StrategyExecutionAuthorizer(store, store)

                with self.assertRaises(StrategyAccessError) as unknown:
                    authorizer.authorize(package.package_hash)
                self.assertEqual(unknown.exception.code, "strategy_trust_required")

                trusted = store.trust_strategy(
                    package.package_hash,
                    decided_by="test-user",
                    reason="local review passed",
                )
                self.assertEqual(trusted.state, StrategyTrustState.TRUSTED)
                authorization = authorizer.authorize(package.package_hash)
                self.assertEqual(
                    authorization.strategy.package_hash,
                    package.package_hash,
                )
                managed = inspect_strategy_package(
                    store.strategy_package_root(package.package_hash)
                )
                point_set = StrategyWorker({package.package_hash}).run(
                    managed,
                    {
                        "strategy": managed.strategy,
                        "selected_regions": [
                            {
                                "id": "street",
                                "rings": [
                                    {
                                        "index": 0,
                                        "role": "outer",
                                        "loop": [
                                            [0.0, -1.25, 2.0],
                                            [40.0, -1.25, 2.0],
                                            [40.0, 1.25, 2.0],
                                            [0.0, 1.25, 2.0],
                                        ],
                                    }
                                ],
                            }
                        ],
                        "seed": 20260805,
                    },
                )
                self.assertGreater(point_set["point_count"], 0)

                revoked = store.revoke_strategy(
                    package.package_hash,
                    decided_by="test-user",
                    reason="manual revoke",
                )
                self.assertEqual(revoked.state, StrategyTrustState.REVOKED)
                self.assertEqual(store.trust_event_count(package.package_hash), 2)
                with self.assertRaises(StrategyAccessError) as rejected:
                    authorizer.authorize(package.package_hash)
                self.assertEqual(rejected.exception.code, "strategy_trust_revoked")

    def test_reinstall_is_idempotent_but_hash_reuse_with_other_bytes_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with LibraryStore.open(root / "library") as store:
                package, first = install(store, NATURAL_ROOT, "通用自然聚落")
                _, second = install(store, NATURAL_ROOT, "通用自然聚落")
                self.assertEqual(first, second)

                changed = root / "changed"
                shutil.copytree(NATURAL_ROOT, changed)
                with (changed / "generator.py").open("a", encoding="utf-8") as stream:
                    stream.write("\n# different bytes\n")
                with self.assertRaises(StrategyInstallConflictError):
                    store.install_verified_strategy(
                        changed,
                        package_id=package.package_id,
                        package_version=package.package_version,
                        package_hash=package.package_hash,
                        display_name="通用自然聚落",
                    )

    def test_managed_package_tampering_is_detected_before_lookup_returns(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with LibraryStore.open(Path(temporary) / "library") as store:
                package, _ = install(store, NATURAL_ROOT, "通用自然聚落")
                managed = store.strategy_package_root(package.package_hash)
                with (managed / "generator.py").open("a", encoding="utf-8") as stream:
                    stream.write("\n# tampered\n")

                with self.assertRaises(LibraryIntegrityError):
                    store.get_strategy_by_hash(package.package_hash)

    def test_open_cleans_interrupted_staging_without_touching_installed_package(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "library"
            with LibraryStore.open(root) as store:
                package, _ = install(store, NATURAL_ROOT, "通用自然聚落")
            orphan = root / "strategy-packages" / ".staging" / "interrupted"
            orphan.mkdir()
            (orphan / "partial.py").write_text("partial", encoding="utf-8")

            with LibraryStore.open(root) as reopened:
                self.assertFalse(orphan.exists())
                self.assertIsNotNone(
                    reopened.get_strategy_by_hash(package.package_hash)
                )

    def test_multiple_process_style_connections_serialize_short_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            library_root = Path(temporary) / "library"
            with LibraryStore.open(library_root):
                pass

            def install_from(source: Path, name: str) -> str:
                with LibraryStore.open(library_root) as store:
                    package, _ = install(store, source, name)
                    store.trust_strategy(package.package_hash, decided_by="worker")
                    return package.package_hash

            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = (
                    executor.submit(install_from, NATURAL_ROOT, "通用自然聚落"),
                    executor.submit(install_from, STREET_ROOT, "通用行道树"),
                )
                hashes = {future.result(timeout=30) for future in futures}

            with LibraryStore.open(library_root) as store:
                self.assertEqual(
                    {item.package_hash for item in store.list_strategies()},
                    hashes,
                )
                self.assertTrue(
                    all(
                        store.get_strategy_trust(package_hash).state
                        is StrategyTrustState.TRUSTED
                        for package_hash in hashes
                    )
                )

    def test_concurrent_reinstall_of_same_package_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            library_root = Path(temporary) / "library"
            package = inspect_strategy_package(NATURAL_ROOT)
            with LibraryStore.open(library_root):
                pass

            def reinstall() -> str:
                with LibraryStore.open(library_root) as store:
                    info = store.install_verified_strategy(
                        package.root,
                        package_id=package.package_id,
                        package_version=package.package_version,
                        package_hash=package.package_hash,
                        display_name="通用自然聚落",
                    )
                    return info.library_entry_id

            with ThreadPoolExecutor(max_workers=2) as executor:
                entries = {
                    future.result(timeout=30)
                    for future in (executor.submit(reinstall), executor.submit(reinstall))
                }
            self.assertEqual(len(entries), 1)

    def test_pre_release_schema_is_refused_instead_of_migrated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "library"
            with LibraryStore.open(root):
                pass

            connection = sqlite3.connect(root / "library.db")
            try:
                connection.execute(
                    "UPDATE schema_metadata SET value = '7' "
                    "WHERE key = 'schema_version'"
                )
                connection.commit()
            finally:
                connection.close()

            with self.assertRaises(LibraryMigrationError):
                LibraryStore.open(root)

    def test_newer_schema_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "library"
            with LibraryStore.open(root):
                pass
            connection = sqlite3.connect(root / "library.db")
            try:
                connection.execute(
                    "UPDATE schema_metadata SET value = '999' "
                    "WHERE key = 'schema_version'"
                )
                connection.commit()
            finally:
                connection.close()

            with self.assertRaises(LibrarySchemaError):
                LibraryStore.open(root)


if __name__ == "__main__":
    unittest.main()
