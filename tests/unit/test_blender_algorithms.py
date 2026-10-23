from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "worker-runtime" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
    str(ROOT / "packages" / "core" / "src"),
    str(ROOT / "adapters" / "blender-worker" / "src"),
]

from verdflow.blender_worker.algorithms import (  # noqa: E402
    boundary_vertex_loops,
    connected_face_islands,
    dominant_projected_boundary_loops,
    first_matching_rule,
    logical_texture_path,
    polygon_area_xy,
    rule_match_evidence,
    validate_rule_pack,
)


class BlenderAlgorithmTests(unittest.TestCase):
    def test_polygon_area_is_stable_for_large_world_coordinates(self) -> None:
        origin = 1_000_000_000_000.0
        self.assertEqual(
            polygon_area_xy(
                [
                    (origin, origin, 0.0),
                    (origin + 4.0, origin, 0.0),
                    (origin + 4.0, origin + 3.0, 0.0),
                    (origin, origin + 3.0, 0.0),
                ]
            ),
            12.0,
        )

    def test_texture_path_preserves_only_source_relative_semantics(self) -> None:
        root = Path("D:/project/source")
        relative, scope = logical_texture_path(
            "D:/project/source/textures/ground/grass.png",
            (root,),
        )
        self.assertEqual(relative, "textures/ground/grass.png")
        self.assertEqual(scope, "source_relative")

        fallback, fallback_scope = logical_texture_path(
            "D:/shared/textures/grass.png",
            (root,),
        )
        self.assertEqual(fallback, "grass.png")
        self.assertEqual(fallback_scope, "external_filename_only")

    def test_connected_faces_merge_but_separate_faces_remain_islands(self) -> None:
        islands = connected_face_islands(
            {
                0: [(0, 1), (1, 4), (3, 4), (0, 3)],
                1: [(1, 2), (2, 5), (4, 5), (1, 4)],
                2: [(10, 11), (11, 12), (12, 13), (10, 13)],
            }
        )

        self.assertEqual(islands, [[0, 1], [2]])

    def test_boundary_removes_shared_edges(self) -> None:
        loops = boundary_vertex_loops(
            [
                [(0, 1), (1, 4), (3, 4), (0, 3)],
                [(1, 2), (2, 5), (4, 5), (1, 4)],
            ]
        )

        self.assertEqual(len(loops), 1)
        self.assertEqual(set(loops[0]), {0, 1, 2, 3, 4, 5})

    def test_projected_boundary_uses_dominant_face_orientation(self) -> None:
        loops = dominant_projected_boundary_loops(
            {
                0: [(0, 1), (1, 2), (2, 3), (0, 3)],
                1: [(4, 7), (6, 7), (5, 6), (4, 5)],
                2: [(0, 4), (1, 5), (0, 1), (4, 5)],
            },
            {
                0: [(0, 0, 1), (2, 0, 1), (2, 2, 1), (0, 2, 1)],
                1: [(0, 0, 0), (0, 2, 0), (2, 2, 0), (2, 0, 0)],
                2: [(0, 0, 1), (0, 0, 0), (2, 0, 0), (2, 0, 1)],
            },
        )

        self.assertEqual(len(loops), 1)
        self.assertEqual(set(loops[0]), {0, 1, 2, 3})

    def test_contains_rules_require_explicit_permission(self) -> None:
        rule_pack = {
            "rules": [
                {
                    "id": "green",
                    "match": {"material_name_contains": ["grass"]},
                }
            ]
        }

        with self.assertRaisesRegex(ValueError, "without permission"):
            validate_rule_pack(rule_pack)
        validate_rule_pack(rule_pack, allow_contains=True)

    def test_exact_material_match_is_independent_of_blender(self) -> None:
        evidence = rule_match_evidence(
            {
                "match": {
                    "material_names": ["Grass_Test"],
                    "object_names": [],
                }
            },
            material_name="Grass_Test",
            object_name="Ground",
            texture_values=[],
        )

        self.assertEqual(evidence, ["material_name:Grass_Test"])

    def test_first_matching_rule_preserves_rule_pack_order(self) -> None:
        match = first_matching_rule(
            [
                {"id": "first", "match": {"object_names": ["Ground"]}},
                {
                    "id": "second",
                    "match": {"material_names": ["Grass_Test"]},
                },
            ],
            material_name="Grass_Test",
            object_name="Ground",
            texture_values=[],
        )

        self.assertEqual(match, ("first", ["object_name:Ground"]))


if __name__ == "__main__":
    unittest.main()
