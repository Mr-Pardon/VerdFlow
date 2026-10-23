from __future__ import annotations

import copy
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "library-store" / "src"),
    str(ROOT / "packages" / "planting" / "src"),
    str(ROOT / "adapters" / "strategy-worker" / "src"),
]

from verdflow.library_store import (  # noqa: E402
    AssetCatalogConflictError,
    LibraryStore,
)
from verdflow.planting import (  # noqa: E402
    load_catalog_yaml,
    load_profile_yaml,
    prepare_asset_catalog_document,
    prepare_role_binding_profile_document,
)
from verdflow.strategy_worker import inspect_strategy_package  # noqa: E402


ASSET_ROOT = ROOT / "asset-packs" / "dts-default"
STRATEGY_ROOT = ROOT / "strategy-packs" / "universal-natural-grove"


def _catalog() -> dict:
    return load_catalog_yaml(
        (ASSET_ROOT / "catalog.yaml").read_text(encoding="utf-8")
    )


def _profile() -> dict:
    return load_profile_yaml(
        (
            ASSET_ROOT / "profiles" / "universal-natural-grove.yaml"
        ).read_text(encoding="utf-8")
    )


class CurrentAssetCatalogTests(unittest.TestCase):
    def test_one_mutable_catalog_identity_uses_generation_for_concurrency(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with LibraryStore.open(Path(temporary)) as store:
                created = store.save_asset_catalog(
                    prepare_asset_catalog_document(_catalog()),
                    expected_generation=0,
                )
                updated_document = _catalog()
                updated_document["display_name"] = "Updated Catalog"
                updated = store.save_asset_catalog(
                    prepare_asset_catalog_document(updated_document),
                    expected_generation=created.generation,
                )

                self.assertEqual(created.catalog_entry_id, updated.catalog_entry_id)
                self.assertEqual(updated.generation, 2)
                self.assertEqual(len(store.list_asset_catalogs(target_type="dts")), 1)
                self.assertEqual(
                    store.load_asset_catalog_document(
                        "dts", updated.catalog_id
                    ).display_name,
                    "Updated Catalog",
                )

    def test_stale_catalog_generation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with LibraryStore.open(Path(temporary)) as store:
                store.save_asset_catalog(
                    prepare_asset_catalog_document(_catalog()),
                    expected_generation=0,
                )
                changed = _catalog()
                changed["display_name"] = "Changed"
                with self.assertRaisesRegex(
                    AssetCatalogConflictError,
                    "generation is stale",
                ):
                    store.save_asset_catalog(
                        prepare_asset_catalog_document(changed),
                        expected_generation=0,
                    )

    def test_catalog_delete_does_not_cascade_into_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with LibraryStore.open(Path(temporary)) as store:
                package = inspect_strategy_package(STRATEGY_ROOT)
                store.install_verified_strategy(
                    package.root,
                    package_id=package.package_id,
                    package_version=package.package_version,
                    package_hash=package.package_hash,
                    display_name=package.strategy["display_name"],
                )
                catalog = store.save_asset_catalog(
                    prepare_asset_catalog_document(_catalog()),
                    expected_generation=0,
                )
                profile = store.save_role_binding_profile(
                    prepare_role_binding_profile_document(_profile()),
                    expected_generation=0,
                )

                removal = store.delete_asset_catalog(
                    catalog.target_type,
                    catalog.catalog_id,
                )

                self.assertTrue(removal.removed)
                self.assertEqual(removal.affected_profiles, 1)
                self.assertIsNotNone(
                    store.get_role_binding_profile(profile.profile_id)
                )

    def test_legacy_catalog_revision_is_not_part_of_the_document(self) -> None:
        legacy = copy.deepcopy(_catalog())
        legacy["catalog_revision"] = "r1"
        with self.assertRaisesRegex(Exception, "catalog_revision"):
            prepare_asset_catalog_document(legacy)


if __name__ == "__main__":
    unittest.main()
