from __future__ import annotations

from collections import Counter
import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages" / "contracts" / "src"))
sys.path.insert(0, str(ROOT / "packages" / "planting" / "src"))

from verdflow.planting import (  # noqa: E402
    build_effective_binding_from_profile,
    load_catalog_yaml,
    load_profile_yaml,
    load_strategy_yaml,
    resolve_asset_assignments,
    validate_point_set_v2,
)


STRATEGY_ROOT = ROOT / "strategy-packs" / "universal-street-tree"
ASSET_ROOT = ROOT / "asset-packs" / "dts-default"


def load_generator():
    path = STRATEGY_ROOT / "generator.py"
    spec = importlib.util.spec_from_file_location("verdflow_test_street_tree", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load strategy generator: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rectangle_region(region_id: str, width: float, length: float = 40.0) -> dict:
    half_width = width * 0.5
    return {
        "id": region_id,
        "rings": [
            {
                "index": 0,
                "role": "outer",
                "loop": [
                    [0.0, -half_width, 2.0],
                    [length, -half_width, 2.0],
                    [length, half_width, 2.0],
                    [0.0, half_width, 2.0],
                ],
            }
        ],
    }


class UniversalStreetTreePackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.strategy = load_strategy_yaml(
            (STRATEGY_ROOT / "strategy.yaml").read_text(encoding="utf-8")
        )
        cls.catalog = load_catalog_yaml(
            (ASSET_ROOT / "catalog.yaml").read_text(encoding="utf-8")
        )
        cls.profile = load_profile_yaml(
            (ASSET_ROOT / "profiles" / "universal-street-tree.yaml").read_text(
                encoding="utf-8"
            )
        )
        cls.generator = load_generator()

    def test_strategy_source_and_output_are_target_neutral(self) -> None:
        source = (STRATEGY_ROOT / "generator.py").read_text(encoding="utf-8")
        strategy_source = (STRATEGY_ROOT / "strategy.yaml").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("SM_", source)
        self.assertNotIn("SM_", strategy_source)

        point_set = self.generator.generate(
            {
                "strategy": self.strategy,
                "selected_regions": [
                    rectangle_region("single", 1.0),
                    rectangle_region("double", 2.5),
                    rectangle_region("triple", 5.0),
                ],
                "seed": 2026071301,
            }
        )
        validate_point_set_v2(point_set)
        for point in point_set["points"]:
            self.assertNotIn("asset", point)
            self.assertNotIn("asset_id", point)
            self.assertNotIn("scale", point)
            self.assertNotIn("scale_xyz", point)

    def test_width_modes_emit_expected_roles_and_tree_in_every_mode(self) -> None:
        expected_roles = {
            "single": {"narrow_center_shrub", "tree_accent"},
            "double": {
                "double_left_shrub",
                "double_right_shrub",
                "tree_accent",
            },
            "triple": {"outer_shrub", "middle_shrub", "tree_accent"},
        }
        for region_id, width in (("single", 1.0), ("double", 2.5), ("triple", 5.0)):
            with self.subTest(region_id=region_id):
                point_set = self.generator.generate(
                    {
                        "strategy": self.strategy,
                        "selected_regions": [rectangle_region(region_id, width)],
                        "seed": 2026071301,
                    }
                )
                roles = {point["role_key"] for point in point_set["points"]}
                self.assertEqual(roles, expected_roles[region_id])

    def test_dts_profile_recovers_assets_and_scale_ranges(self) -> None:
        point_set = self.generator.generate(
            {
                "strategy": self.strategy,
                "selected_regions": [
                    rectangle_region("single", 1.0),
                    rectangle_region("double", 2.5),
                    rectangle_region("triple", 5.0),
                ],
                "seed": 2026071301,
            }
        )
        snapshot = build_effective_binding_from_profile(
            self.strategy,
            self.profile,
        )
        assignments = resolve_asset_assignments(
            point_set,
            snapshot,
            random_seed=2026071301,
        )

        expected_asset = {
            "narrow_center_shrub": "SM_CornusAlba_02",
            "double_left_shrub": "SM_CornusAlba_02",
            "double_right_shrub": "SM_CornusAlba_02",
            "outer_shrub": "SM_CornusAlba_02",
            "middle_shrub": "SM_UlmusMacrocarpaHance_2",
            "tree_accent": "SM_Oak_1D",
        }
        ranges = {
            "SM_CornusAlba_02": (0.8, 1.2),
            "SM_UlmusMacrocarpaHance_2": (0.6, 1.0),
            "SM_Oak_1D": (0.3, 0.5),
        }
        role_counts = Counter(point["role_key"] for point in point_set["points"])
        asset_counts = Counter()
        for assignment in assignments["assignments"]:
            asset_id = expected_asset[assignment["role_key"]]
            self.assertEqual(assignment["asset_id"], asset_id)
            asset_counts[asset_id] += 1
            low, high = ranges[asset_id]
            scale = assignment["target_scale_xyz"][0]
            self.assertGreaterEqual(scale, low)
            self.assertLessEqual(scale, high)

        self.assertEqual(
            asset_counts["SM_CornusAlba_02"],
            sum(
                role_counts[role]
                for role in (
                    "narrow_center_shrub",
                    "double_left_shrub",
                    "double_right_shrub",
                    "outer_shrub",
                )
            ),
        )
        self.assertEqual(
            asset_counts["SM_UlmusMacrocarpaHance_2"], role_counts["middle_shrub"]
        )
        self.assertEqual(asset_counts["SM_Oak_1D"], role_counts["tree_accent"])

    def test_rotation_is_randomized_without_changing_seeded_positions(self) -> None:
        context = {
            "strategy": self.strategy,
            "selected_regions": [rectangle_region("street", 5.0, 80.0)],
            "seed": 2026071301,
        }

        first = self.generator.generate(context)
        repeated = self.generator.generate(context)
        changed_seed = self.generator.generate({**context, "seed": 2026071302})

        first_positions = [
            point["generated_position"] for point in first["points"]
        ]
        first_rotations = [
            point["rotation_quaternion_xyzw"] for point in first["points"]
        ]
        self.assertEqual(first, repeated)
        self.assertEqual(
            first_positions,
            [point["generated_position"] for point in changed_seed["points"]],
        )
        self.assertNotEqual(
            first_rotations,
            [point["rotation_quaternion_xyzw"] for point in changed_seed["points"]],
        )
        self.assertGreater(
            len({tuple(rotation) for rotation in first_rotations}),
            1,
        )


if __name__ == "__main__":
    unittest.main()
