from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import math
from pathlib import Path
import sys
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
STRATEGY_ROOT = ROOT / "strategy-packs" / "lawn-feature-groups"
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "worker-runtime" / "src"),
    str(ROOT / "packages" / "planting" / "src"),
    str(ROOT / "adapters" / "strategy-worker" / "src"),
]

from verdflow.planting import PlantingDocumentError, load_strategy_yaml, validate_point_set_v2  # noqa: E402
from verdflow.strategy_worker import StrategyExecutionError, StrategyWorker, inspect_strategy_package  # noqa: E402


class LawnFeatureGroupsPackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.package = inspect_strategy_package(STRATEGY_ROOT)
        cls.worker = StrategyWorker({cls.package.package_hash})

    def test_package_generates_stable_target_neutral_point_set(self) -> None:
        context = {
            "strategy": self.package.strategy,
            "selected_regions": [_region_document("region_b", 150.0), _region_document("region_a", 0.0)],
            "seed": 17,
        }
        first = self.worker.run(self.package, context)
        repeated = self.worker.run(self.package, context)
        reordered = self.worker.run(
            self.package,
            {**context, "selected_regions": list(reversed(context["selected_regions"]))},
        )

        self.assertEqual(first, repeated)
        self.assertEqual(first, reordered)
        validate_point_set_v2(first)
        self.assertEqual(
            {point["role_key"] for point in first["points"]},
            {
                "lawn_groundcover",
                "edge_groundcover",
                "feature_anchor",
                "surrounding_shrub",
                "outer_accent",
            },
        )
        self.assertTrue(all("asset_id" not in point and "scale" not in point for point in first["points"]))
        self.assertNotIn("SM_", (STRATEGY_ROOT / "generator.py").read_text(encoding="utf-8"))
        self.assertNotIn("SM_", (STRATEGY_ROOT / "strategy.yaml").read_text(encoding="utf-8"))

    def test_points_respect_region_and_group_composition(self) -> None:
        point_set = self.worker.run(
            self.package,
            {
                "strategy": self.package.strategy,
                "selected_regions": [_region_document("region_a", 0.0)],
                "seed": 43,
            },
        )
        feature_groups: dict[str, list[dict]] = defaultdict(list)
        for point in point_set["points"]:
            x, y, z = point["generated_position"]
            self.assertTrue(_in_test_region(x, y))
            self.assertEqual(z, 2.0)
            if point["role_key"] == "lawn_groundcover":
                self.assertGreaterEqual(_boundary_distance(x, y), 0.5 - 1e-8)
            elif point["role_key"] == "edge_groundcover":
                self.assertLess(_boundary_distance(x, y), 0.5 + 1e-8)
            else:
                feature_groups[point["group_id"]].append(point)

        self.assertGreaterEqual(len(feature_groups), 2)
        for group in feature_groups.values():
            counts = Counter(point["role_key"] for point in group)
            self.assertIn(counts["feature_anchor"], {0, 1})
            self.assertIn(counts["surrounding_shrub"], {2, 3, 4})
            self.assertIn(counts["outer_accent"], {2, 3})

    def test_group_density_and_anchor_probability_are_independent_controls(self) -> None:
        context = {
            "strategy": self.package.strategy,
            "selected_regions": [_region_document("region_a", 0.0)],
            "seed": 17,
        }
        no_groups = self.worker.run(
            self.package,
            {
                **context,
                "args": {"strategy_parameters": {"feature_group_density": 0.0}},
            },
        )
        no_anchors = self.worker.run(
            self.package,
            {
                **context,
                "args": {"strategy_parameters": {"feature_anchor_probability": 0.0}},
            },
        )
        all_anchors = self.worker.run(
            self.package,
            {
                **context,
                "args": {"strategy_parameters": {"feature_anchor_probability": 1.0}},
            },
        )
        self.assertEqual(
            {point["role_key"] for point in no_groups["points"]},
            {"lawn_groundcover", "edge_groundcover"},
        )
        self.assertEqual(_role_count(no_anchors, "feature_anchor"), 0)
        self.assertEqual(
            _role_count(all_anchors, "feature_anchor"),
            _feature_group_count(all_anchors),
        )

    def test_anchored_group_layers_stay_close_to_the_main_plant(self) -> None:
        point_set = self.worker.run(
            self.package,
            {
                "strategy": self.package.strategy,
                "selected_regions": [_region_document("region_a", 0.0)],
                "seed": 43,
                "args": {"strategy_parameters": {"feature_anchor_probability": 1.0}},
            },
        )
        feature_groups: dict[str, list[dict]] = defaultdict(list)
        for point in point_set["points"]:
            if point["role_key"] not in _GROUNDCOVER_ROLES:
                feature_groups[point["group_id"]].append(point)

        self.assertTrue(feature_groups)
        for group in feature_groups.values():
            anchor = next(point for point in group if point["role_key"] == "feature_anchor")
            anchor_xy = anchor["generated_position"][:2]
            shrubs = [point for point in group if point["role_key"] == "surrounding_shrub"]
            accents = [point for point in group if point["role_key"] == "outer_accent"]
            self.assertTrue(all(_distance_to(anchor_xy, point) <= 1.45 for point in shrubs))
            self.assertTrue(all(_distance_to(anchor_xy, point) <= 2.05 for point in accents))

    def test_groundcover_layers_are_perturbed_and_spacing_remains_adjustable(self) -> None:
        context = {
            "strategy": self.package.strategy,
            "selected_regions": [_region_document("region_a", 0.0)],
            "seed": 17,
        }
        baseline = self.worker.run(self.package, context)
        sparse = self.worker.run(
            self.package,
            {
                **context,
                "args": {"strategy_parameters": {"groundcover_spacing_m": 3.0}},
            },
        )
        self.assertLess(_role_count(sparse, "lawn_groundcover"), _role_count(baseline, "lawn_groundcover"))
        self.assertGreater(_role_count(baseline, "edge_groundcover"), 0)
        lawn_points = [
            point["generated_position"][:2]
            for point in baseline["points"]
            if point["role_key"] == "lawn_groundcover"
        ]
        nearest_distances = [
            min(math.dist(point, other) for other in lawn_points if other != point)
            for point in lawn_points[:120]
        ]
        self.assertGreater(max(nearest_distances) - min(nearest_distances), 0.15)

    def test_group_count_limits_clamp_the_density_result_per_region(self) -> None:
        small_context = {
            "strategy": self.package.strategy,
            "selected_regions": [_small_region_document()],
            "seed": 43,
        }
        natural = self.worker.run(self.package, small_context)
        minimum = self.worker.run(
            self.package,
            {
                **small_context,
                "args": {
                    "strategy_parameters": {
                        "feature_group_density": 2.0,
                        "feature_group_min_count": 3,
                    }
                },
            },
        )
        capped = self.worker.run(
            self.package,
            {
                "strategy": self.package.strategy,
                "selected_regions": [_region_document("region_a", 0.0)],
                "seed": 43,
                "args": {
                    "strategy_parameters": {
                        "feature_group_density": 4.0,
                        "feature_group_max_count": 2,
                    }
                },
            },
        )

        self.assertEqual(_feature_group_count(natural), 2)
        self.assertEqual(_feature_group_count(minimum), 3)
        self.assertEqual(_feature_group_count(capped), 2)

    def test_exact_two_group_request_keeps_complete_group_composition(self) -> None:
        point_set = self.worker.run(
            self.package,
            {
                "strategy": self.package.strategy,
                "selected_regions": [_small_region_document()],
                "seed": 137,
                "args": {
                    "strategy_parameters": {
                        "feature_group_density": 1.0,
                        "feature_group_min_count": 2,
                        "feature_group_max_count": 2,
                        "feature_anchor_probability": 1.0,
                    }
                },
            },
        )
        groups: dict[str, list[dict]] = defaultdict(list)
        for point in point_set["points"]:
            if point["role_key"] not in _GROUNDCOVER_ROLES:
                groups[point["group_id"]].append(point)

        self.assertEqual(len(groups), 2)
        for group in groups.values():
            counts = Counter(point["role_key"] for point in group)
            self.assertEqual(counts["feature_anchor"], 1)
            self.assertIn(counts["surrounding_shrub"], {2, 3, 4})
            self.assertIn(counts["outer_accent"], {2, 3})

    def test_balanced_centers_cover_distinct_concave_areas(self) -> None:
        point_set = self.worker.run(
            self.package,
            {
                "strategy": self.package.strategy,
                "selected_regions": [_concave_region_document()],
                "seed": 137,
                "args": {
                    "strategy_parameters": {
                        "feature_group_density": 0.1,
                        "feature_group_min_count": 3,
                        "feature_group_max_count": 3,
                        "feature_anchor_probability": 1.0,
                    }
                },
            },
        )
        anchors = [
            point["generated_position"]
            for point in point_set["points"]
            if point["role_key"] == "feature_anchor"
        ]

        self.assertEqual(len(anchors), 3)
        self.assertTrue(any(x < 60.0 for x, _y, _z in anchors))
        self.assertTrue(any(x > 108.0 for x, _y, _z in anchors))
        self.assertTrue(any(y > 64.0 for _x, y, _z in anchors))

    def test_extremely_narrow_region_falls_back_to_lawn_only(self) -> None:
        point_set = self.worker.run(
            self.package,
            {
                "strategy": self.package.strategy,
                "selected_regions": [
                    {
                        "id": "narrow",
                        "rings": [
                            {
                                "index": 0,
                                "role": "outer",
                                "loop": [[0.0, 0.0, 0.0], [80.0, 0.0, 0.0], [80.0, 4.0, 0.0], [0.0, 4.0, 0.0]],
                            }
                        ],
                    }
                ],
                "seed": 17,
            },
        )
        self.assertGreater(point_set["point_count"], 0)
        self.assertEqual(
            {point["role_key"] for point in point_set["points"]},
            _GROUNDCOVER_ROLES,
        )

    def test_generation_parameter_contract_matches_runtime_bounds(self) -> None:
        contract = self.package.strategy["generation_parameters"]
        self.assertEqual(
            [parameter["key"] for parameter in contract],
            [
                "groundcover_spacing_m",
                "feature_group_density",
                "feature_anchor_probability",
                "feature_group_min_count",
                "feature_group_max_count",
            ],
        )
        self.assertTrue(all(parameter["effect"] for parameter in contract))
        self.assertEqual(
            {parameter["key"]: parameter["default"] for parameter in contract},
            {
                "groundcover_spacing_m": 1.2,
                "feature_group_density": 3.0,
                "feature_anchor_probability": 0.75,
                "feature_group_min_count": 0,
                "feature_group_max_count": 0,
            },
        )
        self.assertEqual(
            {parameter["key"]: (parameter["min"], parameter["max"]) for parameter in contract},
            {
                "groundcover_spacing_m": (0.5, 4.0),
                "feature_group_density": (0.0, 4.0),
                "feature_anchor_probability": (0.0, 1.0),
                "feature_group_min_count": (0, 50),
                "feature_group_max_count": (0, 50),
            },
        )

    def test_invalid_parameter_declarations_and_overrides_are_rejected(self) -> None:
        strategy = deepcopy(self.package.strategy)
        strategy["generation_parameters"][0]["default"] = True
        with self.assertRaises(PlantingDocumentError):
            load_strategy_yaml(yaml.safe_dump(strategy))
        with self.assertRaisesRegex(StrategyExecutionError, "unknown_parameter"):
            self.worker.run(
                self.package,
                {
                    "strategy": self.package.strategy,
                    "selected_regions": [_region_document("region_a", 0.0)],
                    "seed": 17,
                    "args": {"strategy_parameters": {"unknown_parameter": 1.0}},
                },
            )

        with self.assertRaisesRegex(StrategyExecutionError, "non-negative integer"):
            self.worker.run(
                self.package,
                {
                    "strategy": self.package.strategy,
                    "selected_regions": [_region_document("region_a", 0.0)],
                    "seed": 17,
                    "args": {
                        "strategy_parameters": {"feature_group_min_count": 1.5}
                    },
                },
            )


def _role_count(point_set: dict, role_key: str) -> int:
    return sum(point["role_key"] == role_key for point in point_set["points"])


_GROUNDCOVER_ROLES = {"lawn_groundcover", "edge_groundcover"}


def _feature_group_count(point_set: dict) -> int:
    return len(
        {
            point["group_id"]
            for point in point_set["points"]
            if point["role_key"] not in _GROUNDCOVER_ROLES
        }
    )


def _distance_to(origin: list[float], point: dict) -> float:
    x, y, _z = point["generated_position"]
    return math.hypot(x - origin[0], y - origin[1])


def _region_document(region_id: str, x: float) -> dict:
    return {
        "id": region_id,
        "rings": [
            {
                "index": 0,
                "role": "outer",
                "loop": [[x, 0.0, 2.0], [x + 120.0, 0.0, 2.0], [x + 120.0, 90.0, 2.0], [x, 90.0, 2.0]],
            },
            {
                "index": 1,
                "role": "hole",
                "loop": [[x + 45.0, 30.0, 2.0], [x + 75.0, 30.0, 2.0], [x + 75.0, 60.0, 2.0], [x + 45.0, 60.0, 2.0]],
            },
        ],
    }


def _small_region_document() -> dict:
    return {
        "id": "small_region",
        "rings": [
            {
                "index": 0,
                "role": "outer",
                "loop": [
                    [0.0, 4.0, 2.0],
                    [6.0, 0.0, 2.0],
                    [24.0, 0.0, 2.0],
                    [30.0, 5.0, 2.0],
                    [29.0, 19.0, 2.0],
                    [23.0, 24.0, 2.0],
                    [5.0, 23.0, 2.0],
                    [0.0, 18.0, 2.0],
                ],
            }
        ],
    }


def _concave_region_document() -> dict:
    return {
        "id": "concave",
        "rings": [
            {
                "index": 0,
                "role": "outer",
                "loop": [
                    [0.0, 0.0, 2.0],
                    [166.0, 0.0, 2.0],
                    [166.0, 44.0, 2.0],
                    [108.0, 44.0, 2.0],
                    [108.0, 112.0, 2.0],
                    [52.0, 112.0, 2.0],
                    [52.0, 64.0, 2.0],
                    [0.0, 64.0, 2.0],
                ],
            }
        ],
    }


def _in_test_region(x: float, y: float) -> bool:
    return 0.0 <= x <= 120.0 and 0.0 <= y <= 90.0 and not (45.0 < x < 75.0 and 30.0 < y < 60.0)


def _boundary_distance(x: float, y: float) -> float:
    outer_distance = min(x, 120.0 - x, y, 90.0 - y)
    if 45.0 <= x <= 75.0:
        hole_distance = min(abs(y - 30.0), abs(y - 60.0))
    elif 30.0 <= y <= 60.0:
        hole_distance = min(abs(x - 45.0), abs(x - 75.0))
    else:
        hole_distance = min(
            math.hypot(x - corner_x, y - corner_y)
            for corner_x, corner_y in ((45.0, 30.0), (75.0, 30.0), (75.0, 60.0), (45.0, 60.0))
        )
    return min(outer_distance, hole_distance)


if __name__ == "__main__":
    unittest.main()
