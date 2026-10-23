from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
STRATEGY_ROOT = ROOT / "strategy-packs" / "layered-ecological-community"
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "worker-runtime" / "src"),
    str(ROOT / "packages" / "planting" / "src"),
    str(ROOT / "adapters" / "strategy-worker" / "src"),
]
sys.path.insert(0, str(STRATEGY_ROOT))

from generator import DEFAULT_PARAMETERS, PARAMETER_BOUNDS  # noqa: E402
from verdflow.planting import (  # noqa: E402
    PlantingDocumentError,
    build_effective_binding_from_profile,
    load_profile_yaml,
    load_strategy_yaml,
    resolve_asset_assignments,
    validate_point_set_v2,
)
from verdflow.strategy_worker import (  # noqa: E402
    StrategyExecutionError,
    StrategyWorker,
    inspect_strategy_package,
)


class LayeredEcologicalCommunityPackTests(unittest.TestCase):
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

        self.assertEqual(first, repeated)
        reordered = self.worker.run(
            self.package,
            {**context, "selected_regions": list(reversed(context["selected_regions"]))},
        )
        self.assertEqual(first, reordered)
        validate_point_set_v2(first)
        for point in first["points"]:
            x, y, z = point["generated_position"]
            local_x = x - (150.0 if point["region_id"] == "region_b" else 0.0)
            self.assertTrue(0.0 <= local_x <= 120.0 and 0.0 <= y <= 90.0)
            self.assertFalse(45.0 < local_x < 75.0 and 30.0 < y < 60.0)
            self.assertEqual(z, 2.0)
        self.assertFalse(first["offset_applied"])
        self.assertEqual(
            {point["role_key"] for point in first["points"]},
            {
                "canopy_anchor",
                "herbaceous_companion",
                "medium_shrub_structure",
                "low_shrub_matrix",
            },
        )
        self.assertTrue(
            all("asset_id" not in point and "scale" not in point for point in first["points"])
        )
        self.assertNotIn("SM_", (STRATEGY_ROOT / "generator.py").read_text(encoding="utf-8"))
        self.assertNotIn("SM_", (STRATEGY_ROOT / "strategy.yaml").read_text(encoding="utf-8"))

    def test_generation_parameters_override_the_frozen_defaults(self) -> None:
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
                "args": {
                    "strategy_parameters": {
                        "overall_density": 0.13,
                        "canopy_density": 2.0,
                    }
                },
            },
        )
        self.assertLess(sparse["point_count"], baseline["point_count"])

    def test_generation_parameter_contract_is_complete_and_matches_defaults(self) -> None:
        contract = self.package.strategy["generation_parameters"]
        self.assertEqual(
            [parameter["key"] for parameter in contract[:3]],
            [
                "overall_density",
                "canopy_density",
                "canopy_field_contrast",
            ],
        )
        self.assertTrue(all(parameter["effect"] for parameter in contract))
        self.assertEqual(
            {parameter["key"]: parameter["default"] for parameter in contract},
            {
                key: getattr(DEFAULT_PARAMETERS, key)
                for key in (
                    "overall_density",
                    "canopy_density",
                    "canopy_field_contrast",
                    "canopy_spacing_scale",
                    "shrub_density",
                    "layer_blend",
                )
            },
        )
        self.assertEqual(
            {
                parameter["key"]: (parameter["min"], parameter["max"])
                for parameter in contract
            },
            {
                key: bounds
                for key, bounds in PARAMETER_BOUNDS.items()
                if key != "cluster_scale"
            },
        )

    def test_internal_cluster_scale_is_not_a_public_override(self) -> None:
        with self.assertRaisesRegex(StrategyExecutionError, "cluster_scale"):
            self.worker.run(
                self.package,
                {
                    "strategy": self.package.strategy,
                    "selected_regions": [_region_document("region_a", 0.0)],
                    "seed": 17,
                    "args": {
                        "strategy_parameters": {"cluster_scale": 2.0},
                    },
                },
            )

    def test_invalid_parameter_declarations_are_rejected(self) -> None:
        mutations = [
            {"default": 2.0},
            {"min": 2.0, "max": 1.0},
            {"default": float("nan")},
            {"max": float("inf")},
            {"step": 0},
            {"default": True},
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                strategy = deepcopy(self.package.strategy)
                strategy["generation_parameters"][0].update(mutation)
                with self.assertRaises(PlantingDocumentError):
                    load_strategy_yaml(yaml.safe_dump(strategy))
        missing_effect = deepcopy(self.package.strategy)
        del missing_effect["generation_parameters"][0]["effect"]
        duplicate_key = deepcopy(self.package.strategy)
        duplicate_key["generation_parameters"].append(
            deepcopy(duplicate_key["generation_parameters"][0])
        )
        for strategy in (missing_effect, duplicate_key):
            with self.assertRaises(PlantingDocumentError):
                load_strategy_yaml(yaml.safe_dump(strategy))

    def test_default_dts_profile_resolves_the_verified_role_mapping(self) -> None:
        point_set = self.worker.run(
            self.package,
            {
                "strategy": self.package.strategy,
                "selected_regions": [_region_document("region_a", 0.0)],
                "seed": 17,
            },
        )
        profile = load_profile_yaml(
            (
                ROOT
                / "asset-packs"
                / "dts-default"
                / "profiles"
                / "layered-ecological-community.yaml"
            ).read_text(encoding="utf-8")
        )
        snapshot = build_effective_binding_from_profile(
            self.package.strategy,
            profile,
        )
        assignments = resolve_asset_assignments(
            point_set,
            snapshot,
            random_seed=17,
        )
        expected_assets = {
            "canopy_anchor": "SM_tree01_01",
            "herbaceous_companion": "SM_A_PinusMugo",
            "medium_shrub_structure": "SM_UlmusMacrocarpaHance_2",
            "low_shrub_matrix": "SM_CornusAlba_02",
        }
        for assignment in assignments["assignments"]:
            self.assertEqual(
                assignment["asset_id"],
                expected_assets[assignment["role_key"]],
            )


def _region_document(region_id: str, x: float) -> dict:
    return {
        "id": region_id,
        "rings": [
            {
                "index": 0,
                "role": "outer",
                "loop": [
                    [x, 0.0, 2.0],
                    [x + 120.0, 0.0, 2.0],
                    [x + 120.0, 90.0, 2.0],
                    [x, 90.0, 2.0],
                ],
            },
            {
                "index": 1,
                "role": "hole",
                "loop": [
                    [x + 45.0, 30.0, 2.0],
                    [x + 75.0, 30.0, 2.0],
                    [x + 75.0, 60.0, 2.0],
                    [x + 45.0, 60.0, 2.0],
                ],
            },
        ],
    }


if __name__ == "__main__":
    unittest.main()
