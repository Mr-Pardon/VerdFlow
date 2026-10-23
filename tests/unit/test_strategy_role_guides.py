from __future__ import annotations

import copy
from dataclasses import asdict
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(path) for parent in (ROOT / "packages", ROOT / "apps", ROOT / "adapters")
               for path in parent.glob("*/src")]

from verdflow.planting import semantic_hash  # noqa: E402
from verdflow.strategy_worker import (  # noqa: E402
    StrategyWorker, inspect_strategy_package, open_strategy_package_archive, write_strategy_package_archive,
)
from verdflow.system_host import SystemHost, SystemHostConfig, StrategyRoleInfo  # noqa: E402
from verdflow.system_host.strategy_role_guides import present_strategy_roles  # noqa: E402


def raw_roles(document):
    return tuple(StrategyRoleInfo(**{key: row[key] for key in (
        "role_key", "display_name", "description", "required", "assignment_policy",
    )}) for row in document["roles"])


class StrategyRoleGuideTests(unittest.TestCase):
    def test_package_owned_guides_preserve_declared_role_contracts(self):
        for name, labels in (
            ("layered-ecological-community-v2", ["铺底植被", "主景植被"]),
            ("lawn-feature-groups", ["主体草坪", "收边草坪", "中心主景", "组团灌木", "外围点缀"]),
            ("universal-street-tree", ["沿线乔木", "单排灌木", "左排灌木", "右排灌木", "两侧灌木", "中排灌木"]),
        ):
            with self.subTest(strategy=name):
                package = inspect_strategy_package(ROOT / "strategy-packs" / name)
                document = copy.deepcopy(package.strategy)
                original = raw_roles(document)
                roles = present_strategy_roles(document, original)
                self.assertEqual([role.display_name for role in roles], labels)
                self.assertEqual({role.role_key: (role.display_name, role.description, role.required, role.assignment_policy) for role in roles},
                                 {role.role_key: (role.display_name, role.description, role.required, role.assignment_policy) for role in original})
                self.assertEqual(document, package.strategy)
                self.assertEqual(inspect_strategy_package(package.root).package_hash, package.package_hash)
                self.assertTrue(all(role.guide.diagram for role in roles))
                self.assertTrue(all(mark.role_key in {role.role_key for role in roles}
                                    for role in roles for mark in role.guide.diagram))

    def test_missing_package_declaration_has_no_application_fallback(self):
        package = inspect_strategy_package(ROOT / "strategy-packs/universal-natural-grove")
        original = raw_roles(package.strategy)
        self.assertIs(present_strategy_roles(package.strategy, original), original)
        other = inspect_strategy_package(ROOT / "strategy-packs/lawn-feature-groups")
        document = copy.deepcopy(other.strategy)
        del document["extensions"]["role_presentation"]
        original = raw_roles(document)
        self.assertIs(present_strategy_roles(document, original), original)
        self.assertFalse((ROOT / "apps/system-host/src/verdflow/system_host/role-guides.json").exists())

    def test_valid_package_presentation_works_without_builtin_identity(self):
        document, roles = self.custom_fixture()
        shown = present_strategy_roles(document, roles)
        self.assertEqual(shown[0].display_name, "铺底")
        self.assertEqual(shown[0].guide.suggested_assets, ("地被",))
        self.assertEqual(shown[0].guide.diagram[0].kind, "shrub")
        self.assertIsNone(roles[0].guide)

    def test_invalid_optional_metadata_never_blocks_strategy(self):
        for damage in ("unknown-role", "nan", "script-mark", "missing-group", "wrong-version", "too-many-marks",
                       "invalid-label", "oversized-number"):
            with self.subTest(damage=damage):
                document, roles = self.custom_fixture()
                guide = document["extensions"]["role_presentation"]
                if damage == "unknown-role":
                    guide["roles"]["unknown"] = guide["roles"].pop("ground")
                elif damage == "nan":
                    guide["groups"][0]["diagram"][0]["x"] = float("nan")
                elif damage == "script-mark":
                    guide["groups"][0]["diagram"][0]["kind"] = "script"
                elif damage == "missing-group":
                    guide["groups"] = []
                elif damage == "wrong-version":
                    guide["version"] = "2"
                elif damage == "invalid-label":
                    guide["roles"]["ground"] = None
                elif damage == "oversized-number":
                    guide["groups"][0]["diagram"][0]["x"] = 10 ** 1000
                else:
                    guide["groups"][0]["diagram"] *= 129
                self.assertIs(present_strategy_roles(document, roles), roles)

    def test_installed_package_identity_and_trust_are_unchanged(self):
        package = inspect_strategy_package(ROOT / "strategy-packs/layered-ecological-community-v2")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with SystemHost(SystemHostConfig(library_path=root / "library")) as system:
                installed = system.install_strategy(package.root)
                trusted = system.trust_strategy(package.package_hash)
                detail = system.get_strategy(package.package_hash)
                self.assertEqual(detail.summary.package_hash, package.package_hash)
                self.assertEqual(detail.summary.strategy_version, "2.2.2")
                self.assertEqual([role.display_name for role in detail.roles], ["铺底植被", "主景植被"])
                self.assertTrue(detail.summary.executable)
                self.assertEqual(detail.summary.trust_state, trusted.trust_state)
                self.assertEqual(installed.summary.package_hash, detail.summary.package_hash)
                self.assertIsInstance(asdict(detail.roles[0])["guide"]["diagram"], tuple)

    def test_custom_author_template_needs_no_application_registration(self):
        template = ROOT / "packages/authoring-kits/src/verdflow/authoring_kits/kits/vfs-author/assets/strategy-template"
        package = inspect_strategy_package(template)
        with tempfile.TemporaryDirectory() as temporary:
            with SystemHost(SystemHostConfig(library_path=Path(temporary) / "library")) as system:
                system.install_strategy(template)
                detail = system.get_strategy(package.package_hash)
                self.assertEqual(detail.summary.strategy_id, "example_strategy")
                self.assertEqual(detail.roles[0].display_name, package.strategy["roles"][0]["display_name"])
                self.assertEqual(detail.roles[0].guide.group_name, "Main structure")
                self.assertTrue(detail.roles[0].guide.diagram)

    @staticmethod
    def custom_fixture():
        return {"extensions": {"role_presentation": {
            "version": "1",
            "roles": {"ground": {"suggested_assets": ["地被"]}},
            "groups": [{"id": "base", "name": "底层", "role_keys": ["ground"],
                        "diagram": [{"role_key": "ground", "kind": "shrub", "x": 50, "y": 30}]}],
        }}}, (StrategyRoleInfo("ground", "铺底", "低矮铺底植物", True, "weighted_pool"),)

    def test_guides_travel_with_vfs_and_are_read_without_an_application_registry(self):
        for name in ("layered-ecological-community-v2", "lawn-feature-groups", "universal-street-tree"):
            with self.subTest(strategy=name), tempfile.TemporaryDirectory() as temporary:
                original = inspect_strategy_package(ROOT / "strategy-packs" / name)
                archive = Path(temporary) / "strategy.vfs"
                write_strategy_package_archive(original, archive)
                with open_strategy_package_archive(archive) as opened:
                    self.assertEqual(opened.package.strategy, original.strategy)
                    self.assertEqual(opened.package.package_hash, original.package_hash)
                    roles = present_strategy_roles(opened.package.strategy, raw_roles(opened.package.strategy))
                    self.assertTrue(all(role.guide.diagram for role in roles))

    def test_presentation_patch_keeps_previous_point_and_rotation_results(self):
        expected = {
            "layered-ecological-community-v2": "sha256:ff01ee5604c80188590ab602ee7c218edeef501d125db6ed7776179db5adfdc5",
            "lawn-feature-groups": "sha256:801abef14e09115e8cd9997bafa9762995b17187c6b59aebec7c8d3510e39f51",
            "universal-street-tree": "sha256:81a36756d7493177a84961ef04d0614ec1e91b039dc9d6fc5c24889d06b46bae",
        }
        region = {"id": "presentation_fixture", "rings": [{"index": 0, "role": "outer", "loop": [
            [0, 0, 2], [100, 0, 2], [100, 6, 2], [0, 6, 2],
        ]}]}
        for name, digest in expected.items():
            with self.subTest(strategy=name):
                package = inspect_strategy_package(ROOT / "strategy-packs" / name)
                result = StrategyWorker({package.package_hash}).run(package, {
                    "strategy": package.strategy, "selected_regions": [region], "seed": 17,
                })
                self.assertEqual(semantic_hash(result["points"]), digest)

    def test_production_layers_do_not_know_builtin_strategy_identity(self):
        builtin_ids = ("layered_ecological_community", "lawn_feature_groups",
                       "universal_street_tree", "universal_natural_grove")
        roots = [ROOT / "apps" / name / "src" for name in (
            "studio", "dts-live-webui", "system-host", "core-host", "studio-service",
        )] + [ROOT / "packages" / name / "src" for name in ("core", "planting", "role-guide-ui")]
        for root in roots:
            for path in root.rglob("*"):
                if path.suffix in {".py", ".ts", ".vue", ".json"}:
                    source = path.read_text("utf-8")
                    self.assertFalse(any(identity in source for identity in builtin_ids), str(path.relative_to(ROOT)))


if __name__ == "__main__":
    unittest.main()
