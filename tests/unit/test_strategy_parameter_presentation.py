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

from verdflow.planting import PlantingDocumentError
from verdflow.planting.validation import validate_strategy
from verdflow.strategy_worker import inspect_strategy_package, open_strategy_package_archive, write_strategy_package_archive
from verdflow.system_host import SystemHost, SystemHostConfig


class StrategyParameterPresentationTests(unittest.TestCase):
    def test_role_references_are_optional_validated_and_do_not_change_values(self):
        package = inspect_strategy_package(ROOT / "strategy-packs/layered-ecological-community-v2")
        document = copy.deepcopy(package.strategy)
        parameter = document["generation_parameters"][0]
        original_default = parameter["default"]
        for roles in (None, ["groundcover_matrix"], ["groundcover_matrix", "focal_structure"]):
            with self.subTest(roles=roles):
                parameter.pop("visible_for_roles", None)
                if roles is not None:
                    parameter["visible_for_roles"] = roles
                self.assertEqual(validate_strategy(document)["generation_parameters"][0]["default"], original_default)
        for roles in ([], ["missing"], ["focal_structure", "focal_structure"], "focal_structure"):
            with self.subTest(roles=roles), self.assertRaises(PlantingDocumentError):
                parameter["visible_for_roles"] = roles
                validate_strategy(document)

    def test_builtin_associations_follow_actual_algorithm_scope(self):
        layered = inspect_strategy_package(ROOT / "strategy-packs/layered-ecological-community-v2").strategy
        parameters = {item["key"]: item.get("visible_for_roles", []) for item in layered["generation_parameters"]}
        self.assertEqual(parameters["groundcover_spacing_m"], ["groundcover_matrix"])
        self.assertEqual(parameters["focal_spacing_m"], ["focal_structure"])
        self.assertEqual(parameters["focal_core_ratio_percent"], ["focal_structure"])
        self.assertEqual(parameters["groundcover_coverage_percent"], ["groundcover_matrix"])
        self.assertEqual(parameters["patch_scale_m"], [])
        lawn = inspect_strategy_package(ROOT / "strategy-packs/lawn-feature-groups").strategy
        parameters = {item["key"]: item["visible_for_roles"] for item in lawn["generation_parameters"]}
        self.assertEqual(parameters["groundcover_spacing_m"], ["lawn_groundcover"])
        for key in ("feature_group_density", "feature_anchor_probability", "feature_group_min_count", "feature_group_max_count"):
            self.assertEqual(parameters[key], ["feature_anchor", "surrounding_shrub", "outer_accent"])

    def test_vfs_and_type_projection_preserve_package_owned_associations(self):
        for name in ("layered-ecological-community-v2", "lawn-feature-groups"):
            with self.subTest(strategy=name), tempfile.TemporaryDirectory() as temporary:
                package = inspect_strategy_package(ROOT / "strategy-packs" / name)
                archive = Path(temporary) / "strategy.vfs"
                write_strategy_package_archive(package, archive)
                with open_strategy_package_archive(archive) as opened:
                    self.assertEqual(opened.package.strategy, package.strategy)
                root = Path(temporary) / "library"
                with SystemHost(SystemHostConfig(library_path=root)) as system:
                    system.install_strategy(package.root)
                    detail = system.get_strategy(package.package_hash)
                    for source, projected in zip(package.strategy["generation_parameters"], detail.generation_parameters):
                        self.assertEqual(list(projected.visible_for_roles), source.get("visible_for_roles", []))
                        self.assertEqual(asdict(projected)["visible_for_roles"], projected.visible_for_roles)


if __name__ == "__main__":
    unittest.main()
