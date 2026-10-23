from __future__ import annotations

from pathlib import Path
import copy
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "asset-package" / "src"),
    str(ROOT / "packages" / "worker-runtime" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
    str(ROOT / "packages" / "library-store" / "src"),
    str(ROOT / "packages" / "planting" / "src"),
    str(ROOT / "packages" / "profile-package" / "src"),
    str(ROOT / "packages" / "core" / "src"),
    str(ROOT / "adapters" / "blender-worker" / "src"),
    str(ROOT / "adapters" / "dts" / "src"),
    str(ROOT / "adapters" / "model-api" / "src"),
    str(ROOT / "adapters" / "strategy-worker" / "src"),
    str(ROOT / "apps" / "core-host" / "src"),
    str(ROOT / "apps" / "system-host" / "src"),
]

from verdflow.library_store import LibraryStore  # noqa: E402
from verdflow.planting import (  # noqa: E402
    load_catalog_yaml,
    load_profile_yaml,
    prepare_asset_catalog_document,
    prepare_role_binding_profile_document,
)
from verdflow.strategy_worker import inspect_strategy_package  # noqa: E402
from verdflow.system_host import (  # noqa: E402
    DtsLiveConfigurationRequest,
    SystemHost,
    SystemHostConfig,
    RoleBindingProfileSaveRequest,
)


STRATEGY_ROOT = ROOT / "strategy-packs" / "universal-street-tree"
ASSET_ROOT = ROOT / "asset-packs" / "dts-default"


class DtsLiveConfigurationTests(unittest.TestCase):
    def test_plan_tweaks_freeze_as_overrides_without_catalog_or_profile_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package_hash, profile = _prepare_layered_library(root / "library")
            overrides = copy.deepcopy(profile["assignments"])
            role_key = next(iter(overrides))
            overrides[role_key]["enabled"] = False
            with SystemHost(_config(root)) as system:
                system.delete_asset_catalog("dts", "dts_default")
                original = system.get_role_binding_profile(profile["profile_id"])
                snapshot = system.freeze_dts_live_execution(DtsLiveConfigurationRequest(
                    strategy_package_hash=package_hash, profile_id=profile["profile_id"],
                    assignments=overrides, generation_args={"strategy_parameters": {"focal_spacing_m": 12}},
                ))
                self.assertEqual(snapshot.binding_overrides, overrides)
                self.assertEqual(snapshot.profile_semantic_hash, original.summary.semantic_hash)
                self.assertEqual(snapshot.asset_source_kind, "profile")
                self.assertEqual(system.get_role_binding_profile(profile["profile_id"]).summary.generation, original.summary.generation)
                self.assertNotEqual(snapshot.effective_binding_semantic_hash, system.freeze_dts_live_execution(
                    DtsLiveConfigurationRequest(strategy_package_hash=package_hash, profile_id=profile["profile_id"])
                ).effective_binding_semantic_hash)

    def test_profile_presets_can_be_saved_preserved_cleared_and_exchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package_hash, profile = _prepare_layered_library(root / "library")
            identity = profile["strategy"]
            fields = dict(profile_id=profile["profile_id"], display_name="Preset",
                strategy_id=identity["strategy_id"], strategy_version=identity["strategy_version"],
                target_type="dts", catalog_id="dts_default", assignments=profile["assignments"])
            with SystemHost(_config(root)) as system:
                saved = system.save_role_binding_profile(RoleBindingProfileSaveRequest(**fields,
                    expected_generation=1, strategy_parameters={"focal_spacing_m": 16}))
                self.assertEqual(saved.strategy_parameters, {"focal_spacing_m": 16})
                unchanged = system.save_role_binding_profile(RoleBindingProfileSaveRequest(**fields,
                    expected_generation=saved.summary.generation))
                self.assertEqual(unchanged.strategy_parameters, saved.strategy_parameters)
                output = root / "preset.vfp"
                system.export_role_binding_profile_package(profile["profile_id"], output)
                system.delete_role_binding_profile(profile["profile_id"])
                system.import_role_binding_profile_package(output)
                restored = system.get_role_binding_profile(profile["profile_id"])
                self.assertEqual(restored.strategy_parameters, saved.strategy_parameters)
                cleared = system.save_role_binding_profile(RoleBindingProfileSaveRequest(**fields,
                    expected_generation=restored.summary.generation, replace_strategy_parameters=True))
                self.assertIsNone(cleared.strategy_parameters)
                with self.assertRaisesRegex(ValueError, "Unknown public"):
                    system.save_role_binding_profile(RoleBindingProfileSaveRequest(**fields,
                        expected_generation=cleared.summary.generation, strategy_parameters={"unknown": 1}))

    def test_live_freezes_profile_values_below_run_parameters(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package_hash, profile = _prepare_layered_library(root / "library", {"focal_spacing_m": 16, "groundcover_spacing_m": 4})
            with SystemHost(_config(root)) as system:
                snapshot = system.freeze_dts_live_execution(DtsLiveConfigurationRequest(
                    strategy_package_hash=package_hash, profile_id=profile["profile_id"], random_seed=99,
                    generation_args={"strategy_parameters": {"focal_spacing_m": 12}}))
            self.assertEqual(snapshot.generation_args["strategy_parameters"]["focal_spacing_m"], 12)
            self.assertEqual(snapshot.generation_args["strategy_parameters"]["groundcover_spacing_m"], 4)
            self.assertIn("groundcover_coverage_percent", snapshot.generation_args["strategy_parameters"])
            self.assertEqual(snapshot.random_seed, 99)

    def test_freezes_exact_trusted_strategy_profile_and_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package_hash, profile_id = _prepare_library(
                root / "library",
                trusted=True,
            )
            with SystemHost(_config(root)) as system:
                self.assertIsNotNone(system.dts_live_connector)
                snapshot = system.freeze_dts_live_execution(
                    DtsLiveConfigurationRequest(
                        strategy_package_hash=package_hash,
                        profile_id=profile_id,
                        random_seed=17,
                        generation_args={
                            "strategy_parameters": {}
                        },
                    )
                )

            self.assertEqual(snapshot.strategy_id, "universal_street_tree")
            self.assertEqual(snapshot.strategy_version, "2.0.2")
            self.assertEqual(snapshot.target_type, "dts")
            self.assertEqual(snapshot.asset_source_kind, "profile")
            self.assertEqual(snapshot.asset_source_id, profile_id)
            self.assertIsNone(snapshot.asset_source_generation)
            self.assertEqual(snapshot.random_seed, 17)
            self.assertEqual(
                snapshot.generation_args,
                {"strategy_parameters": {}},
            )
            self.assertRegex(
                snapshot.effective_binding_semantic_hash,
                r"^sha256:[0-9a-f]{64}$",
            )

    def test_rejects_non_json_generation_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package_hash, profile_id = _prepare_library(
                root / "library",
                trusted=True,
            )
            with SystemHost(_config(root)) as system:
                with self.assertRaisesRegex(ValueError, "valid JSON"):
                    system.freeze_dts_live_execution(
                        DtsLiveConfigurationRequest(
                            strategy_package_hash=package_hash,
                            profile_id=profile_id,
                            generation_args={"invalid": float("nan")},
                        )
                    )

    def test_rejects_untrusted_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package_hash, profile_id = _prepare_library(
                root / "library",
                trusted=False,
            )
            with SystemHost(_config(root)) as system:
                with self.assertRaisesRegex(ValueError, "not trusted"):
                    system.freeze_dts_live_execution(
                        DtsLiveConfigurationRequest(
                            strategy_package_hash=package_hash,
                            profile_id=profile_id,
                        )
                    )

    def test_freezes_temporary_role_mappings_without_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package_hash, _ = _prepare_library(
                root / "library",
                trusted=True,
            )
            profile_path = ASSET_ROOT / "profiles" / "universal-street-tree.yaml"
            assignments = load_profile_yaml(
                profile_path.read_text(encoding="utf-8")
            )["assignments"]
            with SystemHost(_config(root)) as system:
                snapshot = system.freeze_dts_live_execution(
                    DtsLiveConfigurationRequest(
                        strategy_package_hash=package_hash,
                        catalog_id="dts_default",
                        assignments=assignments,
                    )
                )

            self.assertIsNone(snapshot.profile_id)
            self.assertIsNone(snapshot.profile_semantic_hash)
            self.assertEqual(snapshot.asset_source_kind, "catalog")
            self.assertEqual(snapshot.asset_source_id, "dts_default")
            self.assertEqual(snapshot.asset_source_generation, 1)
            self.assertEqual(snapshot.binding_overrides, assignments)
            self.assertRegex(
                snapshot.effective_binding_semantic_hash,
                r"^sha256:[0-9a-f]{64}$",
            )

    def test_rejects_profile_for_a_different_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package_hash, profile_id = _prepare_library(
                root / "library",
                trusted=True,
            )
            natural = inspect_strategy_package(
                ROOT / "strategy-packs" / "universal-natural-grove"
            )
            with LibraryStore.open(root / "library") as store:
                store.install_verified_strategy(
                    natural.root,
                    package_id=natural.package_id,
                    package_version=natural.package_version,
                    package_hash=natural.package_hash,
                    display_name=natural.strategy["display_name"],
                )
                store.trust_strategy(natural.package_hash, decided_by="test-user")

            with SystemHost(_config(root)) as system:
                with self.assertRaisesRegex(ValueError, "does not match"):
                    system.freeze_dts_live_execution(
                        DtsLiveConfigurationRequest(
                            strategy_package_hash=natural.package_hash,
                            profile_id=profile_id,
                        )
                    )

            self.assertNotEqual(package_hash, natural.package_hash)


def _prepare_library(
    library_root: Path,
    *,
    trusted: bool,
) -> tuple[str, str]:
    package = inspect_strategy_package(STRATEGY_ROOT)
    catalog = load_catalog_yaml(
        (ASSET_ROOT / "catalog.yaml").read_text(encoding="utf-8")
    )
    profile_path = ASSET_ROOT / "profiles" / "universal-street-tree.yaml"
    profile = load_profile_yaml(profile_path.read_text(encoding="utf-8"))
    with LibraryStore.open(library_root) as store:
        store.install_verified_strategy(
            package.root,
            package_id=package.package_id,
            package_version=package.package_version,
            package_hash=package.package_hash,
            display_name=package.strategy["display_name"],
        )
        if trusted:
            store.trust_strategy(package.package_hash, decided_by="test-user")
        store.save_asset_catalog(
            prepare_asset_catalog_document(catalog),
            expected_generation=0,
        )
        store.save_role_binding_profile(
            prepare_role_binding_profile_document(profile),
            expected_generation=0,
        )
    return package.package_hash, profile["profile_id"]


def _config(root: Path) -> SystemHostConfig:
    return SystemHostConfig(
        library_path=root / "library",
        system_path=root / "system",
    )


def _prepare_layered_library(root: Path, preset: dict | None = None) -> tuple[str, dict]:
    _prepare_library(root, trusted=True)
    package = inspect_strategy_package(ROOT / "strategy-packs/layered-ecological-community-v2")
    profile = load_profile_yaml((ASSET_ROOT / "profiles/layered-ecological-community-v2.yaml").read_text(encoding="utf-8"))
    if preset is not None:
        profile["strategy_parameters"] = preset
    with LibraryStore.open(root) as store:
        store.install_verified_strategy(package.root, package_id=package.package_id,
            package_version=package.package_version, package_hash=package.package_hash,
            display_name=package.strategy["display_name"])
        store.trust_strategy(package.package_hash, decided_by="test-user")
        store.save_role_binding_profile(prepare_role_binding_profile_document(profile), expected_generation=0)
    return package.package_hash, profile


if __name__ == "__main__":
    unittest.main()
