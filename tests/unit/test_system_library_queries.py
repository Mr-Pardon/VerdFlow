from __future__ import annotations

import copy
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(path)
    for parent in (ROOT / "packages", ROOT / "apps", ROOT / "adapters")
    for path in parent.glob("*/src")
]

from verdflow.library_store import LibraryStore  # noqa: E402
from verdflow.planting import (  # noqa: E402
    load_catalog_yaml,
    load_profile_yaml,
    prepare_asset_catalog_document,
)
from verdflow.strategy_worker import inspect_strategy_package  # noqa: E402
from verdflow.system_host import (  # noqa: E402
    AssetCatalogSaveRequest,
    RoleBindingProfileSaveRequest,
    SystemHost,
    SystemHostConfig,
)


STRATEGY_ROOT = ROOT / "strategy-packs" / "universal-natural-grove"
LAYERED_STRATEGY_ROOT = ROOT / "strategy-packs" / "layered-ecological-community"
ASSET_ROOT = ROOT / "asset-packs" / "dts-default"


class SystemLibraryProfileTests(unittest.TestCase):
    def test_plan_summaries_only_include_enabled_plants_and_copy_is_self_contained(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package, catalog, profile = _prepare_library(root / "library")
            assignments = _editable_assignments(profile)
            role_keys = list(assignments)
            assignments[role_keys[-1]]["enabled"] = False
            with SystemHost(_config(root)) as system:
                created = system.save_role_binding_profile(RoleBindingProfileSaveRequest(
                    profile_id="plan_source", display_name="Source", strategy_id=package.package_id,
                    strategy_version=package.package_version, target_type="dts", catalog_id=catalog["catalog_id"],
                    assignments=assignments, strategy_parameters={}, expected_generation=0,
                ))
                summary = system.list_role_binding_profiles("dts")[0]
                self.assertEqual(summary.enabled_role_count, len(assignments) - 1)
                expected_ids = {candidate["asset_id"] for key, assignment in assignments.items()
                                if assignment.get("enabled", True) for candidate in assignment["candidates"]}
                self.assertEqual(summary.asset_count, len(expected_ids))
                self.assertEqual({plant.asset_id for plant in summary.plants}, expected_ids)
                self.assertLessEqual(len(summary.plants), 4)
                self.assertTrue(summary.has_parameter_preset)
                system.delete_asset_catalog("dts", catalog["catalog_id"])
                detached = system.list_role_binding_profiles("dts")[0]
                self.assertEqual(detached.asset_count, summary.asset_count)
                self.assertTrue(all(plant.thumbnail_resource_id is None for plant in detached.plants))
                copied = system.save_role_binding_profile(RoleBindingProfileSaveRequest(
                    source_profile_id="plan_source", display_name="Copy", strategy_id=package.package_id,
                    strategy_version=package.package_version, target_type="dts", catalog_id=None,
                    assignments=assignments, strategy_parameters={}, expected_generation=0,
                ))
                self.assertNotEqual(copied.summary.profile_id, "plan_source")
                self.assertEqual(copied.assignments, created.assignments)
                self.assertEqual(system.get_role_binding_profile("plan_source").summary.generation, 1)
                with self.assertRaisesRegex(ValueError, "retain its strategy"):
                    system.save_role_binding_profile(RoleBindingProfileSaveRequest(
                        source_profile_id="plan_source", display_name="Invalid", strategy_id="other",
                        strategy_version=package.package_version, target_type="dts", catalog_id=None,
                        assignments=assignments, strategy_parameters={},
                    ))

    def test_strategy_and_authoring_kit_exchange_use_portable_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            strategy_path = root / "natural-grove.vfs"
            kit_path = root / "vfs-author.zip"
            with SystemHost(_config(root)) as system:
                installed = system.install_strategy(STRATEGY_ROOT)
                exported = system.export_strategy_package(
                    installed.summary.package_hash,
                    strategy_path,
                )
                self.assertEqual(exported.package_hash, installed.summary.package_hash)
                self.assertTrue(strategy_path.is_file())

                system.uninstall_strategy(installed.summary.package_hash)
                imported = system.import_strategy_package(strategy_path)
                self.assertFalse(imported.already_installed)
                self.assertEqual(imported.package_hash, installed.summary.package_hash)
                self.assertIsNone(imported.strategy.trust_state)
                self.assertTrue(
                    system.import_strategy_package(strategy_path).already_installed
                )

                kit = system.export_authoring_kit("vfs-author", kit_path)
                self.assertEqual(kit.skill_id, "vfs-author")
                with zipfile.ZipFile(kit_path, mode="r") as archive:
                    self.assertIn("vfs-author/SKILL.md", archive.namelist())

    def test_strategy_query_projects_runtime_parameter_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = inspect_strategy_package(LAYERED_STRATEGY_ROOT)
            with LibraryStore.open(root / "library") as store:
                store.install_verified_strategy(
                    package.root,
                    package_id=package.package_id,
                    package_version=package.package_version,
                    package_hash=package.package_hash,
                    display_name=package.strategy["display_name"],
                )
            with SystemHost(_config(root)) as system:
                detail = system.get_strategy(package.package_hash)

            self.assertEqual(detail.default_random_seed, 17)
            self.assertEqual(detail.generation_parameters[0].key, "overall_density")
            self.assertIn("全部植被层次", detail.generation_parameters[0].effect)

    def test_empty_current_catalog_is_visible_to_product_queries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog = load_catalog_yaml(
                (ASSET_ROOT / "catalog.yaml").read_text(encoding="utf-8")
            )
            catalog["catalog_id"] = "empty_catalog"
            catalog["display_name"] = "Empty Catalog"
            catalog["assets"] = []
            catalog["collections"] = []
            with LibraryStore.open(root / "library") as store:
                store.save_asset_catalog(
                    prepare_asset_catalog_document(catalog),
                    expected_generation=0,
                )

            with SystemHost(_config(root)) as system:
                summaries = system.list_asset_catalogs("dts")
                self.assertEqual(len(summaries), 1)
                self.assertEqual(summaries[0].catalog_id, "empty_catalog")
                self.assertEqual(summaries[0].generation, 1)
                page = system.get_asset_catalog_assets(
                    "dts",
                    "empty_catalog",
                )
                self.assertEqual(page.total_count, 0)
                self.assertEqual(page.items, ())

    def test_profile_is_mutable_single_identity_and_deletable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package, catalog, profile = _prepare_library(root / "library")
            request = RoleBindingProfileSaveRequest(
                profile_id="project_grove",
                display_name="项目自然聚落",
                strategy_id=package.package_id,
                strategy_version=package.package_version,
                target_type="dts",
                catalog_id=catalog["catalog_id"],
                assignments=_editable_assignments(profile),
                expected_generation=0,
            )
            with SystemHost(_config(root)) as system:
                created = system.save_role_binding_profile(request)
                self.assertEqual(created.summary.generation, 1)
                self.assertEqual(created.summary.profile_id, "project_grove")
                self.assertEqual(
                    len(system.list_role_binding_profiles("dts")),
                    1,
                )

                assignments = copy.deepcopy(_editable_assignments(profile))
                assignments["low_canopy_filler"]["candidates"][0]["weight"] = 2
                updated = system.save_role_binding_profile(
                    RoleBindingProfileSaveRequest(
                        profile_id="project_grove",
                        display_name="项目自然聚落 调整",
                        strategy_id=package.package_id,
                        strategy_version=package.package_version,
                        target_type="dts",
                        catalog_id=None,
                        assignments=assignments,
                        expected_generation=1,
                    )
                )
                self.assertEqual(updated.summary.generation, 2)
                self.assertEqual(
                    updated.assignments["low_canopy_filler"]["candidates"][0][
                        "weight"
                    ],
                    2.0,
                )
                self.assertTrue(
                    system.delete_role_binding_profile("project_grove").removed
                )
                self.assertEqual(system.list_role_binding_profiles("dts"), ())

    def test_stale_generation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package, catalog, profile = _prepare_library(root / "library")
            request = RoleBindingProfileSaveRequest(
                profile_id="profile_stale",
                display_name="Stale",
                strategy_id=package.package_id,
                strategy_version=package.package_version,
                target_type="dts",
                catalog_id=catalog["catalog_id"],
                assignments=_editable_assignments(profile),
                expected_generation=0,
            )
            with SystemHost(_config(root)) as system:
                system.save_role_binding_profile(request)
                with self.assertRaisesRegex(RuntimeError, "generation is stale"):
                    system.save_role_binding_profile(request)

    def test_profile_asset_metadata_can_be_explicitly_synced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package, catalog, profile = _prepare_library(root / "library")
            role_key = "low_canopy_filler"
            asset_id = profile["assignments"][role_key]["candidates"][0][
                "asset_id"
            ]
            with SystemHost(_config(root)) as system:
                created = system.save_role_binding_profile(
                    RoleBindingProfileSaveRequest(
                        profile_id="profile_sync",
                        display_name="Sync",
                        strategy_id=package.package_id,
                        strategy_version=package.package_version,
                        target_type="dts",
                        catalog_id=catalog["catalog_id"],
                        assignments=_editable_assignments(profile),
                        expected_generation=0,
                    )
                )
                changed = copy.deepcopy(catalog)
                asset = next(
                    item
                    for item in changed["assets"]
                    if item["asset_id"] == asset_id
                )
                asset["reference_radius_m"] = 3.25
                asset["reference_height_m"] = 6.5
                system.save_asset_catalog(
                    AssetCatalogSaveRequest(
                        document=changed,
                        expected_generation=1,
                    )
                )

                drifted = system.get_role_binding_profile("profile_sync")
                self.assertEqual(
                    drifted.catalog_drift[f"{role_key}/{asset_id}"],
                    "metadata_changed",
                )
                synced = system.sync_role_binding_profile_assets(
                    "profile_sync",
                    expected_generation=created.summary.generation,
                )

            self.assertEqual(synced.summary.generation, 2)
            self.assertEqual(synced.assignments[role_key]["candidates"][0]["reference_height_m"], 6.5)
            self.assertEqual(
                synced.assignments[role_key]["candidates"][0][
                    "reference_radius_m"
                ],
                3.25,
            )
            self.assertEqual(
                synced.catalog_drift[f"{role_key}/{asset_id}"],
                "current",
            )


def _prepare_library(library_root: Path):
    package = inspect_strategy_package(STRATEGY_ROOT)
    catalog = load_catalog_yaml(
        (ASSET_ROOT / "catalog.yaml").read_text(encoding="utf-8")
    )
    profile = load_profile_yaml(
        (ASSET_ROOT / "profiles" / "universal-natural-grove.yaml").read_text(
            encoding="utf-8"
        )
    )
    with LibraryStore.open(library_root) as store:
        store.install_verified_strategy(
            package.root,
            package_id=package.package_id,
            package_version=package.package_version,
            package_hash=package.package_hash,
            display_name=package.strategy["display_name"],
        )
        store.save_asset_catalog(
            prepare_asset_catalog_document(catalog),
            expected_generation=0,
        )
    return package, catalog, profile


def _editable_assignments(profile: dict) -> dict:
    return {
        role_key: {
            "candidates": [
                {
                    "asset_id": candidate["asset_id"],
                    "weight": candidate["weight"],
                    "scale": candidate["scale"],
                }
                for candidate in assignment["candidates"]
            ]
        }
        for role_key, assignment in profile["assignments"].items()
    }


def _config(root: Path) -> SystemHostConfig:
    return SystemHostConfig(
        library_path=root / "library",
        system_path=root / "system",
    )


if __name__ == "__main__":
    unittest.main()
