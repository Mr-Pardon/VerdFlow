from __future__ import annotations

import copy
from io import BytesIO
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "planting" / "src"),
]

from verdflow.planting import (  # noqa: E402
    PlantingDocumentError,
    build_effective_binding_from_profile,
    build_effective_binding_snapshot,
    canonical_json_bytes,
    load_catalog_yaml,
    load_profile_yaml,
    load_strategy_yaml,
    resolve_asset_assignments,
    resolve_persisted_asset_assignments,
    write_persisted_asset_assignments,
)
from verdflow.planting.canonical import canonical_row_bytes  # noqa: E402


STRATEGY_ROOT = ROOT / "strategy-packs" / "universal-natural-grove"
ASSET_ROOT = ROOT / "asset-packs" / "dts-default"


def _strategy() -> dict:
    return load_strategy_yaml(
        (STRATEGY_ROOT / "strategy.yaml").read_text(encoding="utf-8")
    )


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


def _points() -> dict:
    return {
        "schema": "verdflow.points",
        "version": "2",
        "strategy": {
            "strategy_id": "universal_natural_grove",
            "strategy_version": "2.0.0",
        },
        "kind": "generated",
        "coordinate_space": "source_scene_world",
        "offset_applied": False,
        "resolution_policy": "generated_position",
        "point_count": 2,
        "points": [
            {
                "id": "point_1",
                "region_id": "region_1",
                "generated_position": [1.0, 2.0, 0.0],
                "role_key": "low_canopy_filler",
                "terrain_sample": None,
                "resolved_position": [1.0, 2.0, 0.0],
                "rotation_quaternion_xyzw": [0.0, 0.0, 0.0, 1.0],
            },
            {
                "id": "point_2",
                "region_id": "region_1",
                "generated_position": [3.0, 4.0, 0.0],
                "role_key": "medium_canopy_accent",
                "terrain_sample": None,
                "resolved_position": [3.0, 4.0, 0.0],
                "rotation_quaternion_xyzw": [0.0, 0.0, 0.0, 1.0],
            },
        ],
    }


class PlantingResolutionTests(unittest.TestCase):
    def test_flat_row_encoding_is_exactly_canonical(self) -> None:
        rows = [
            {}, _points()["points"][0],
            {"id": "cafe\u0301", "optional": None, "value": -0.0, "scale": [-0.0, 0, 1e-25], "bool": False},
            {"id": "\u6811", "size_intent": {"height": -0.0, "tags": ["cafe\u0301", True]}},
            {"caf\u00e9": "non-ascii-key", "vector": [1, 2, 3, 4, 5]},
        ]
        for row in rows:
            with self.subTest(row=row):
                self.assertEqual(canonical_row_bytes(row), canonical_json_bytes(row))

    def test_flat_row_encoding_keeps_validation_errors(self) -> None:
        cycle = []
        cycle.append(cycle)
        rows = [
            {"p": [0, float("nan")]}, {"scale": float("inf")}, {"p": cycle},
            {"caf\u00e9": 1, "cafe\u0301": 2}, {"p": [1, object()]}, {1: "invalid key"},
        ]
        for row in rows:
            with self.subTest(row=row):
                with self.assertRaises(PlantingDocumentError) as original:
                    canonical_json_bytes(row)
                with self.assertRaises(PlantingDocumentError) as optimized:
                    canonical_row_bytes(row)
                self.assertEqual(str(original.exception), str(optimized.exception))

    def test_streaming_assignment_has_identical_canonical_bytes(self) -> None:
        points = _points()
        points["version"] = "3"
        templates = points["points"]
        for template in templates:
            template["path_id"] = template.pop("region_id")
            template["group_id"] = "pair"
        points["points"] = [{**templates[i % 2], "id": f"p_{i}"} for i in range(4100)]
        points["point_count"] = len(points["points"])
        snapshot = build_effective_binding_from_profile(_strategy(), _profile())
        expected = resolve_asset_assignments(points, snapshot, random_seed=42)
        data = json.dumps(points, indent=2).encode("utf-8")
        output = BytesIO()
        write_persisted_asset_assignments(
            BytesIO(data), output, snapshot, random_seed=42,
            expected_point_count=4100, point_kind="generated",
        )
        self.assertEqual(output.getvalue(), canonical_json_bytes(expected))
        with self.assertRaises(PlantingDocumentError):
            write_persisted_asset_assignments(
                BytesIO(data + b"{}"), BytesIO(), snapshot, random_seed=42,
                expected_point_count=4100, point_kind="generated",
            )

    def test_persisted_point_set_path_preserves_assignment_output(self) -> None:
        points = _points()
        snapshot = build_effective_binding_from_profile(_strategy(), _profile())
        expected = resolve_asset_assignments(points, snapshot, random_seed=42)
        progress: list[tuple[str, float]] = []

        actual = resolve_persisted_asset_assignments(
            points,
            snapshot,
            random_seed=42,
            expected_point_count=2,
            progress=lambda stage, value: progress.append((stage, value)),
        )

        self.assertEqual(actual, expected)
        self.assertIn(("resolving_asset_assignments", 1.0), progress)
        self.assertIn(("validating_asset_assignments", 1.0), progress)

    def test_persisted_point_set_path_checks_core_record_count(self) -> None:
        with self.assertRaisesRegex(
            PlantingDocumentError,
            "differs from its Core record",
        ):
            resolve_persisted_asset_assignments(
                _points(),
                build_effective_binding_from_profile(_strategy(), _profile()),
                random_seed=42,
                expected_point_count=3,
            )

    def test_profile_snapshot_is_self_contained_and_deterministic(self) -> None:
        first = build_effective_binding_from_profile(_strategy(), _profile())
        second = build_effective_binding_from_profile(_strategy(), _profile())
        self.assertEqual(first, second)
        self.assertEqual(
            first["source_profile"]["profile_id"],
            "universal_natural_grove.dts_default",
        )
        self.assertEqual(first["target"]["provider_id"], "dts.static-mesh")
        self.assertEqual(first["asset_use"]["source"]["kind"], "profile")

    def test_profile_execution_does_not_read_catalog(self) -> None:
        profile = _profile()
        profile["catalog_hint"] = {"catalog_id": "catalog_that_does_not_exist"}
        snapshot = build_effective_binding_from_profile(_strategy(), profile)
        assigned = resolve_asset_assignments(_points(), snapshot, random_seed=42)
        self.assertEqual(len(assigned["assignments"]), 2)
        self.assertEqual(
            assigned["assignments"][0]["asset_id"],
            "SM_tree01_01",
        )

    def test_manual_mapping_still_resolves_through_catalog(self) -> None:
        assignments = {
            role_key: {
                "candidates": [
                    {
                        "asset_id": assignment["candidates"][0]["asset_id"],
                        "weight": 1,
                        "scale": assignment["candidates"][0]["scale"],
                    }
                ]
            }
            for role_key, assignment in _profile()["assignments"].items()
        }
        snapshot = build_effective_binding_snapshot(
            _strategy(),
            _catalog(),
            catalog_generation=1,
            overrides=assignments,
        )
        self.assertIsNone(snapshot["source_profile"])
        self.assertEqual(snapshot["asset_use"]["source"]["generation"], 1)

    def test_profile_rejects_missing_target_reference(self) -> None:
        profile = copy.deepcopy(_profile())
        del profile["assignments"]["low_canopy_filler"]["candidates"][0][
            "target_asset_ref"
        ]
        with self.assertRaises(PlantingDocumentError):
            build_effective_binding_from_profile(_strategy(), profile)


if __name__ == "__main__":
    unittest.main()
