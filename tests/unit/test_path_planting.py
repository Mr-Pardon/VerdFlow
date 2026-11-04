from pathlib import Path
import copy
import importlib.util
import math
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / package / "src")
    for package in ("contracts", "project-store", "planting")
]
from verdflow.planting import (
    load_strategy_yaml,
    validate_point_set,
    validate_point_set_v2,
    validate_path_document,
    strategy_input_kind,
)
from verdflow.project_store import ProjectStore

PACK = ROOT / "strategy-packs" / "path-paired-placement"


class PathPlantingTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "path_pack_fixture", PACK / "generator.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.generate = module.generate
        self.strategy = load_strategy_yaml(
            (PACK / "strategy.yaml").read_text(encoding="utf-8")
        )
        self.context = {
            "strategy": self.strategy,
            "selected_paths": [
                {"id": "road", "coordinates": [[0, 0, 0], [10, 0, 0], [10, 10, 0]]}
            ],
            "seed": 17,
            "args": {"strategy_parameters": {"spacing_m": 6}},
        }

    def test_continuous_xy_stations_emit_paired_roles_and_do_not_force_endpoint(self):
        points = validate_point_set(self.generate(self.context))["points"]
        expected = [[0, 0, 0], [6, 0, 0], [10, 2, 0], [10, 8, 0]]
        self.assertEqual(
            [point["generated_position"] for point in points[::2]], expected
        )
        for a, b in zip(points[::2], points[1::2]):
            self.assertNotEqual(a["id"], b["id"])
            self.assertEqual(a["group_id"], b["group_id"])
            self.assertEqual(a["generated_position"], b["generated_position"])
            self.assertNotIn("region_id", a)
        with self.assertRaises(ValueError):
            validate_point_set_v2(self.generate(self.context))

    def test_role_switches_preserve_ids_positions_and_seeded_vegetation_rotations(self):
        original = self.generate(self.context)
        self.assertEqual(original, self.generate(self.context))
        disabled = self.generate({**self.context, "excluded_role_keys": ["planter"]})
        self.assertEqual(
            disabled["points"],
            [
                point
                for point in original["points"]
                if point["role_key"] == "vegetation"
            ],
        )

    def test_exact_corner_uses_outgoing_tangent_and_instances_remain_upright(self):
        self.context["args"]["strategy_parameters"]["spacing_m"] = 5
        point = self.generate(self.context)["points"][5]
        self.assertEqual(point["generated_position"], [10, 0, 0])
        quaternion = point["rotation_quaternion_xyzw"]
        self.assertEqual(quaternion[:2], [0, 0])
        self.assertAlmostEqual(quaternion[2], 0)
        self.assertAlmostEqual(quaternion[3], 1)

    def test_tree_pit_local_positive_y_follows_signed_straight_tangent(self):
        for x, y in ((10, 0), (0, 10), (-10, 0), (0, -10), (6, -8), (-6, 8)):
            with self.subTest(tangent=(x, y)):
                self.context["selected_paths"][0]["coordinates"] = [[0, 0, 0], [x, y, 0]]
                planter = self.generate(self.context)["points"][1]
                _, _, z, w = planter["rotation_quaternion_xyzw"]
                self.assertAlmostEqual(-2 * z * w, x / 10)
                self.assertAlmostEqual(1 - 2 * z * z, y / 10)

    def test_round_corner_uses_arc_length_and_planter_tangent(self):
        self.context["args"]["strategy_parameters"].update(
            spacing_m=50, start_offset_m=8 + math.pi / 2, corner_radius_m=2
        )
        tree, planter = validate_point_set(self.generate(self.context))["points"]
        self.assertEqual(tree["generated_position"], planter["generated_position"])
        x, y, z = planter["generated_position"]
        self.assertAlmostEqual(x, 8 + math.sqrt(2))
        self.assertAlmostEqual(y, 2 - math.sqrt(2))
        self.assertEqual(z, 0)
        self.assertAlmostEqual(planter["rotation_quaternion_xyzw"][2], -math.sin(math.pi / 8))
        self.context["seed"] += 1
        new_tree, new_planter = self.generate(self.context)["points"]
        self.assertEqual(planter, new_planter)
        self.assertNotEqual(tree["rotation_quaternion_xyzw"], new_tree["rotation_quaternion_xyzw"])

    def test_adjacent_corners_cap_radius_without_overlapping(self):
        self.context["selected_paths"][0]["coordinates"] = [
            [0, 0, 0], [2, 0, 0], [2, 2, 0], [4, 2, 0]
        ]
        self.context["args"]["strategy_parameters"].update(
            spacing_m=50, start_offset_m=1 + math.pi / 2, corner_radius_m=50
        )
        planter = self.generate(self.context)["points"][1]
        x, y, _ = planter["generated_position"]
        self.assertAlmostEqual(x, 2)
        self.assertAlmostEqual(y, 1)
        self.assertAlmostEqual(planter["rotation_quaternion_xyzw"][2], 0)

    def test_start_offset_short_paths_and_total_instance_budget(self):
        self.context["args"]["strategy_parameters"].update(start_offset_m=3)
        self.assertEqual(
            self.generate(self.context)["points"][0]["generated_position"], [3, 0, 0]
        )
        self.context["args"]["strategy_parameters"].update(start_offset_m=21)
        with self.assertRaisesRegex(ValueError, "短于起点留距"):
            self.generate(self.context)
        self.context["selected_paths"][0]["coordinates"] = [[0, 0, 0], [100000, 0, 0]]
        self.context["args"]["strategy_parameters"].update(
            start_offset_m=0, spacing_m=0.5
        )
        with self.assertRaisesRegex(ValueError, "超过.*上限"):
            self.generate(self.context)

    def test_path_contract_rejects_closed_degenerate_nonfinite_and_duplicate_ids(self):
        original = {
            "schema": "verdflow.paths",
            "version": "1",
            "coordinate_space": "source_scene_world",
            "offset_applied": False,
            "path_count": 1,
            "paths": [{"id": "path", "coordinates": [[0, 0, 0], [10, 0, 0]]}],
        }
        validate_path_document(original)
        for coordinates in (
            [[0, 0, 0], [0, 0, 0]],
            [[0, 0, 0], [10, 0, 0], [0, 0, 0]],
            [[0, 0, 0], [math.inf, 0, 0]],
            [[0, 0, 0], [10**1000, 0, 0]],
        ):
            document = copy.deepcopy(original)
            document["paths"][0]["coordinates"] = coordinates
            with self.subTest(coordinates=coordinates), self.assertRaises(ValueError):
                validate_path_document(document)
        original["paths"].append(copy.deepcopy(original["paths"][0]))
        original["path_count"] = 2
        with self.assertRaises(ValueError):
            validate_path_document(original)
        self.assertEqual(strategy_input_kind({}), "polygon")
        self.assertEqual(strategy_input_kind(self.strategy), "polyline")

    def test_schema_23_upgrade_backs_up_database_and_preserves_existing_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "fixture.verdflow"
            with ProjectStore.create(root, name="Path migration fixture") as store:
                artifact = store.put_bytes(
                    b"historical immutable bytes", media_type="application/octet-stream"
                )
            with closing(sqlite3.connect(root / "project.db")) as connection:
                connection.execute("PRAGMA foreign_keys=OFF")
                connection.execute("DROP TABLE planting_runs")
                connection.execute(
                    """CREATE TABLE planting_runs (
                    planting_run_id TEXT PRIMARY KEY, scene_revision_id TEXT NOT NULL REFERENCES scene_revisions(scene_revision_id),
                    region_set_id TEXT NOT NULL REFERENCES region_sets(region_set_id), producer_job_id TEXT NOT NULL UNIQUE REFERENCES jobs(job_id),
                    strategy_id TEXT NOT NULL, strategy_version TEXT NOT NULL, strategy_package_hash TEXT NOT NULL,
                    generator_id TEXT NOT NULL, generator_version TEXT NOT NULL, random_seed INTEGER NOT NULL, input_hash TEXT NOT NULL, created_at TEXT NOT NULL)"""
                )
                connection.execute("DROP TABLE path_sets")
                connection.execute(
                    "UPDATE schema_metadata SET value='23' WHERE key='schema_version'"
                )
                connection.execute("UPDATE projects SET schema_version=23")
                connection.commit()
            with (
                patch(
                    "verdflow.project_store.store.cleanup_completed_migration_backups"
                ),
                ProjectStore.open(root) as store,
            ):
                with store.open_artifact(artifact.artifact_id) as stream:
                    self.assertEqual(stream.read(), b"historical immutable bytes")
            self.assertEqual(
                len(
                    list(
                        (root / "cache" / "migration-backups").glob(
                            "project-v23-to-v24-*.db"
                        )
                    )
                ),
                1,
            )
            with closing(sqlite3.connect(root / "project.db")) as connection:
                self.assertEqual(
                    connection.execute("PRAGMA foreign_key_check").fetchall(), []
                )
                self.assertIn(
                    "path_set_id",
                    [
                        row[1]
                        for row in connection.execute(
                            "PRAGMA table_info(planting_runs)"
                        )
                    ],
                )
