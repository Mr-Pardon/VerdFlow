from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages" / "contracts" / "src"))
sys.path.insert(0, str(ROOT / "packages" / "worker-runtime" / "src"))
sys.path.insert(0, str(ROOT / "packages" / "planting" / "src"))
sys.path.insert(0, str(ROOT / "adapters" / "strategy-worker" / "src"))

from verdflow.planting import (  # noqa: E402
    build_effective_binding_from_profile,
    load_catalog_yaml,
    load_profile_yaml,
    resolve_asset_assignments,
)
from verdflow.strategy_worker import StrategyWorker, inspect_strategy_package  # noqa: E402


STRATEGY_ROOT = ROOT / "strategy-packs" / "universal-natural-grove"
ASSET_ROOT = ROOT / "asset-packs" / "dts-default"


def rectangle_region(region_id: str, x: float, y: float, width: float, height: float) -> dict:
    return {
        "id": region_id,
        "rings": [
            {
                "index": 0,
                "role": "outer",
                "loop": [
                    [x, y, 2.0],
                    [x + width, y, 2.0],
                    [x + width, y + height, 2.0],
                    [x, y + height, 2.0],
                ],
            }
        ],
    }


class UniversalNaturalGrovePackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.package = inspect_strategy_package(STRATEGY_ROOT)
        cls.worker = StrategyWorker({cls.package.package_hash})
        cls.catalog = load_catalog_yaml(
            (ASSET_ROOT / "catalog.yaml").read_text(encoding="utf-8")
        )
        cls.profile = load_profile_yaml(
            (ASSET_ROOT / "profiles" / "universal-natural-grove.yaml").read_text(
                encoding="utf-8"
            )
        )

    def generate(self) -> dict:
        regions = [
            rectangle_region("patch_a", 0.0, 0.0, 100.0, 100.0),
            rectangle_region("patch_b", 110.0, 0.0, 45.0, 45.0),
        ]
        return self.worker.run(
            self.package,
            {
                "strategy": self.package.strategy,
                "selected_regions": regions,
                "seed": 202607072130,
            },
        )

    def test_worker_generates_target_neutral_points_with_stable_group_mix(self) -> None:
        self.assertNotIn(
            "SM_", (STRATEGY_ROOT / "generator.py").read_text(encoding="utf-8")
        )
        self.assertNotIn(
            "SM_", (STRATEGY_ROOT / "strategy.yaml").read_text(encoding="utf-8")
        )
        point_set = self.generate()
        by_group: dict[str, Counter[str]] = defaultdict(Counter)
        for point in point_set["points"]:
            by_group[point["group_id"]][point["role_key"]] += 1
            self.assertNotIn("asset_id", point)
            self.assertNotIn("scale", point)
            self.assertIsNone(point["terrain_sample"])
            self.assertEqual(point["generated_position"], point["resolved_position"])

        expected_roles = {
            "low_canopy_filler",
            "medium_canopy_accent",
            "evergreen_contrast",
        }
        self.assertEqual({point["role_key"] for point in point_set["points"]}, expected_roles)
        for counts in by_group.values():
            total = sum(counts.values())
            self.assertLessEqual(abs(counts["low_canopy_filler"] - total * 0.5), 1.0)
            self.assertLessEqual(abs(counts["medium_canopy_accent"] - total * 0.35), 1.0)
            self.assertLessEqual(abs(counts["evergreen_contrast"] - total * 0.15), 1.0)

    def test_dts_profile_resolves_assets_and_scale_ranges(self) -> None:
        point_set = self.generate()
        snapshot = build_effective_binding_from_profile(
            self.package.strategy,
            self.profile,
        )
        assignments = resolve_asset_assignments(
            point_set,
            snapshot,
            random_seed=202607072130,
        )
        expected = {
            "low_canopy_filler": ("SM_tree01_01", 0.4, 0.6),
            "medium_canopy_accent": ("SM_Tree0301", 0.7, 1.1),
            "evergreen_contrast": ("SM_A_PinusMugo", 0.8, 1.2),
        }
        for assignment in assignments["assignments"]:
            asset_id, minimum, maximum = expected[assignment["role_key"]]
            self.assertEqual(assignment["asset_id"], asset_id)
            scale = assignment["target_scale_xyz"][0]
            self.assertGreaterEqual(scale, minimum)
            self.assertLessEqual(scale, maximum)


if __name__ == "__main__":
    unittest.main()
