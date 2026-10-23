from __future__ import annotations

import copy
import math
from pathlib import Path
import sys
import unittest
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "planting" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
    str(ROOT / "packages" / "core" / "src"),
    str(ROOT / "adapters" / "dts" / "src"),
]

from verdflow.dts_adapter import (  # noqa: E402
    render_dts_xml,
    render_dts_xml_composition,
)
from verdflow.planting import semantic_hash  # noqa: E402


class DtsXmlAdapterTests(unittest.TestCase):
    def test_renders_offset_model_name_and_scaled_quaternion_matrix(self) -> None:
        point_set, binding, assignments = self._documents()

        document = render_dts_xml(
            point_set,
            binding,
            assignments,
            (10.0, 20.0, 30.0),
        )

        self.assertEqual(document.record_count, 1)
        root = ET.fromstring(document.content)
        point = root.find("ModelPoint")
        assert point is not None
        self.assertEqual(point.findtext("ModelName"), "Oak & Co")
        self.assertEqual(point.findtext("LocationX"), "11.000000")
        self.assertEqual(point.findtext("LocationY"), "22.000000")
        self.assertEqual(point.findtext("LocationZ"), "33.000000")
        self.assertEqual(
            point.findtext("Matrix3"),
            "0.000000,-0.750000,0.000000,"
            "0.500000,0.000000,0.000000,"
            "0.000000,0.000000,1.250000",
        )
        self.assertNotIn(b"-0.000000", document.content)

    def test_rejects_generated_point_set_before_output(self) -> None:
        point_set, binding, assignments = self._documents()
        point_set["kind"] = "generated"
        point_set["resolution_policy"] = "generated_position"
        point_set["points"][0]["terrain_sample"] = None
        point_set["points"][0]["generated_position"] = [1.0, 2.0, 3.0]

        with self.assertRaisesRegex(ValueError, "elevation_resolved"):
            render_dts_xml(point_set, binding, assignments, (0.0, 0.0, 0.0))

    def test_renders_ordered_batches_into_one_xml_document(self) -> None:
        first = self._documents()
        second = tuple(copy.deepcopy(item) for item in first)
        second[0]["points"][0]["id"] = "point_2"
        second[0]["points"][0]["resolved_position"] = [4.0, 5.0, 6.0]
        second[0]["points"][0]["terrain_sample"]["position"] = [
            4.0,
            5.0,
            6.0,
        ]
        second[1]["asset_use"]["assets"][0]["provider_metadata"][
            "model_name"
        ] = "Second Oak"
        second[1]["semantic_hash"] = semantic_hash(
            {key: value for key, value in second[1].items() if key != "semantic_hash"}
        )
        second[2]["assignments"][0]["point_id"] = "point_2"
        second[2]["semantic_hash"] = semantic_hash(
            {key: value for key, value in second[2].items() if key != "semantic_hash"}
        )

        document = render_dts_xml_composition(
            (first, second),
            (10.0, 20.0, 30.0),
        )

        self.assertEqual(document.record_count, 2)
        nodes = ET.fromstring(document.content).findall("ModelPoint")
        self.assertEqual(
            [node.findtext("ModelName") for node in nodes],
            ["Oak & Co", "Second Oak"],
        )
        self.assertEqual(nodes[1].findtext("LocationX"), "14.000000")

    def test_reports_render_progress_for_each_completed_input(self) -> None:
        first = self._documents()
        second = tuple(copy.deepcopy(item) for item in first)
        second[0]["points"][0]["id"] = "point_2"
        second[2]["assignments"][0]["point_id"] = "point_2"
        second[2]["semantic_hash"] = semantic_hash(
            {key: value for key, value in second[2].items() if key != "semantic_hash"}
        )
        updates: list[tuple[int, int]] = []

        document = render_dts_xml_composition(
            (first, second),
            (0.0, 0.0, 0.0),
            progress=lambda completed, total: updates.append((completed, total)),
        )

        self.assertEqual(document.record_count, 2)
        self.assertEqual(updates, [(1, 2), (2, 2)])

    @staticmethod
    def _documents():
        point_set = {
            "schema": "verdflow.points",
            "version": "2",
            "strategy": {
                "strategy_id": "fixture",
                "strategy_version": "1",
            },
            "coordinate_space": "source_scene_world",
            "offset_applied": False,
            "kind": "elevation_resolved",
            "resolution_policy": "sample_hit_else_generated",
            "point_count": 1,
            "points": [
                {
                    "id": "point_1",
                    "region_id": "region_1",
                    "role_key": "tree.canopy",
                    "generated_position": [1.0, 2.0, 0.0],
                    "terrain_sample": {
                        "hit": True,
                        "position": [1.0, 2.0, 3.0],
                        "normal": [0.0, 0.0, 1.0],
                        "target": "terrain",
                    },
                    "resolved_position": [1.0, 2.0, 3.0],
                    "rotation_quaternion_xyzw": [
                        0.0,
                        0.0,
                        math.sqrt(0.5),
                        math.sqrt(0.5),
                    ],
                }
            ],
        }
        binding = _hashed(
            {
                "schema": "verdflow.effective-binding-snapshot",
                "version": "1",
                "strategy": {
                    "strategy_id": "fixture",
                    "strategy_version": "1",
                },
                "target": {
                    "target_type": "dts",
                    "provider_id": "fixture",
                },
                "source_profile": None,
                "override_applied": False,
                "asset_use": {
                    "source": {
                        "kind": "catalog",
                        "catalog_id": "fixture",
                        "generation": 1,
                        "semantic_hash": "sha256:" + "1" * 64,
                    },
                    "assets": [
                        {
                            "asset_id": "oak",
                            "display_name": "Oak",
                            "provider_metadata": {"model_name": "Oak & Co"},
                        }
                    ],
                },
                "assignments": {
                    "tree.canopy": {
                        "candidates": [
                            {
                                "asset_id": "oak",
                                "weight": 1.0,
                                "scale": {"mode": "fixed", "value": 1.0},
                            }
                        ]
                    }
                },
            }
        )
        assignments = _hashed(
            {
                "schema": "verdflow.asset-assignment-set",
                "version": "1",
                "target_type": "dts",
                "resolver": {"id": "fixture", "version": "1"},
                "random_seed": 1,
                "point_set_hash": "sha256:" + "2" * 64,
                "effective_binding_snapshot_hash": "sha256:" + "3" * 64,
                "point_count": 1,
                "assignments": [
                    {
                        "point_id": "point_1",
                        "role_key": "tree.canopy",
                        "asset_id": "oak",
                        "target_scale_xyz": [0.5, 0.75, 1.25],
                    }
                ],
            }
        )
        return point_set, binding, assignments


def _hashed(value: dict):
    result = copy.deepcopy(value)
    result["semantic_hash"] = semantic_hash(result)
    return result


if __name__ == "__main__":
    unittest.main()
