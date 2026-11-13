from __future__ import annotations

from contextlib import contextmanager
from base64 import b64decode
from dataclasses import replace
from io import BytesIO
import json
import math
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zlib


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "asset-package" / "src"),
    str(ROOT / "packages" / "worker-runtime" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
    str(ROOT / "packages" / "library-store" / "src"),
    str(ROOT / "packages" / "planting" / "src"),
    str(ROOT / "packages" / "profile-package" / "src"),
    str(ROOT / "packages" / "core" / "src"),
    str(ROOT / "adapters" / "blender-worker" / "src"),
    str(ROOT / "adapters" / "dts" / "src"),
    str(ROOT / "adapters" / "model-api" / "src"),
    str(ROOT / "adapters" / "strategy-worker" / "src"),
    str(ROOT / "apps" / "core-host" / "src"),
    str(ROOT / "apps" / "system-host" / "src"),
    str(ROOT / "apps" / "studio-service" / "src"),
]

from verdflow.contracts import (  # noqa: E402
    AssetAssignmentSetInfo,
    AssetAssignmentSummary,
    PointRoleSummary,
    PointSetInfo,
    PointSetKind,
    RegionSetInfo,
    RegionSummary,
)
from verdflow.studio_service.viewer_queries import (  # noqa: E402
    ProjectViewerQueries,
    ViewerProjectionError,
    _LruCache,
    _simplify_closed_ring,
)


class _FixtureStore:
    def __init__(self) -> None:
        self.region_set = RegionSetInfo(
            region_set_id="regions_1",
            scene_revision_id="scene_1",
            producer_job_id="job_regions",
            extractor_id="fixture",
            extractor_version="1",
            input_hash="sha256:regions",
            coordinate_frame_id="frame_1",
            geometry_artifact_id="artifact_regions",
            metrics_artifact_id="artifact_metrics",
            region_count=3,
            created_at="2026-08-14T00:00:00+00:00",
        )
        self.regions = (
            self._region("r1", 0, "general_green", 0, 0, 3, 3, 9),
            self._region("r2", 1, "street_tree", 4, 0, 8, 2, 8),
            self._region("r3", 2, "general_green", 20, 20, 24, 24, 16),
        )
        self.point_set = PointSetInfo(
            point_set_id="points_1",
            planting_run_id="run_1",
            parent_point_set_id=None,
            producer_job_id="job_points",
            kind=PointSetKind.GENERATED,
            producer_id="fixture",
            producer_version="1",
            input_hash="sha256:points",
            coordinate_frame_id="frame_1",
            point_artifact_id="artifact_points",
            resolution_policy="generated_position",
            point_count=4,
            sampled_count=0,
            hit_count=0,
            miss_count=0,
            min_x=1,
            min_y=1,
            min_z=0,
            max_x=21,
            max_y=21,
            max_z=0,
            created_at="2026-08-14T00:00:01+00:00",
        )
        self.assignment_set = AssetAssignmentSetInfo(
            asset_assignment_set_id="assignments_1",
            point_set_id="points_1",
            effective_binding_snapshot_id="binding_1",
            producer_job_id="job_assignments",
            target_type="dts",
            resolver_id="fixture",
            resolver_version="1",
            random_seed=7,
            semantic_hash="sha256:assignments",
            assignment_artifact_id="artifact_assignments",
            point_count=4,
            created_at="2026-08-14T00:00:02+00:00",
        )
        self.artifacts = {
            "artifact_regions": self._json_bytes(
                {
                    "schema": "verdflow.regions",
                    "version": "1",
                    "regions": [
                        {
                            "id": region.region_id,
                            "footprint_boundary_xyz": [
                                [
                                    [region.min_x, region.min_y, 0],
                                    [region.max_x, region.min_y, 0],
                                    [region.max_x, region.max_y, 0],
                                    [region.min_x, region.min_y, 0],
                                ]
                            ],
                        }
                        for region in self.regions
                    ],
                }
            ),
            "artifact_points": self._json_bytes(
                {
                    "schema": "verdflow.points",
                    "version": "2",
                    "points": [
                        self._point("p1", "r1", "tree", [1, 1, 0]),
                        self._point("p2", "r1", "shrub", [2, 2, 0]),
                        self._point("p3", "r2", "tree", [6, 1, 0]),
                        self._point("p4", "r3", "tree", [21, 21, 0]),
                    ],
                }
            ),
            "artifact_assignments": self._json_bytes(
                {
                    "schema": "verdflow.asset-assignment-set",
                    "version": "1",
                    "assignments": [
                        self._assignment("p1", "oak", 0.8),
                        self._assignment("p2", "cornus", 1.0),
                        self._assignment("p3", "oak", 1.2),
                        self._assignment("p4", "oak", 0.9),
                    ],
                }
            ),
            "artifact_asset_use": self._json_bytes(
                {
                    "schema": "verdflow.target-asset-use-snapshot",
                    "version": "1",
                    "assets": [
                        {
                            "asset_id": "cornus",
                            "display_name": "白花山茱萸",
                            "reference_radius_m": 1.5,
                            "reference_height_m": 3.0,
                        },
                        {
                            "asset_id": "oak",
                            "display_name": "橡树",
                            "reference_radius_m": 2.0,
                            "reference_height_m": 6.0,
                        },
                    ],
                }
            ),
        }
        self.open_counts: dict[str, int] = {}
        self.occupied: tuple[str, ...] = ()

    def current_planting_composition(self):
        return SimpleNamespace(region_set_id=self.region_set.region_set_id) if self.occupied else None

    def list_planting_batches(self):
        return (SimpleNamespace(selected_region_ids=self.occupied),)

    def get_artifact(self, artifact_id: str):
        return SimpleNamespace(byte_size=len(self.artifacts[artifact_id]))

    def get_region_set(self, region_set_id: str) -> RegionSetInfo:
        assert region_set_id == self.region_set.region_set_id
        return self.region_set

    def list_regions(self, region_set_id: str) -> tuple[RegionSummary, ...]:
        assert region_set_id == self.region_set.region_set_id
        return self.regions

    def get_point_set(self, point_set_id: str) -> PointSetInfo:
        assert point_set_id == self.point_set.point_set_id
        return self.point_set

    def get_planting_run(self, planting_run_id: str):
        assert planting_run_id == self.point_set.planting_run_id
        return SimpleNamespace(region_set_id=self.region_set.region_set_id)

    def list_point_set_roles(self, point_set_id: str) -> tuple[PointRoleSummary, ...]:
        assert point_set_id == self.point_set.point_set_id
        return (PointRoleSummary("shrub", 1), PointRoleSummary("tree", 3))

    def get_assignment_set(self, assignment_set_id: str) -> AssetAssignmentSetInfo:
        assert assignment_set_id == self.assignment_set.asset_assignment_set_id
        return self.assignment_set

    def list_assignment_assets(
        self,
        assignment_set_id: str,
    ) -> tuple[AssetAssignmentSummary, ...]:
        assert assignment_set_id == self.assignment_set.asset_assignment_set_id
        return (
            AssetAssignmentSummary("cornus", 1),
            AssetAssignmentSummary("oak", 3),
        )

    def get_effective_binding_snapshot(self, snapshot_id: str):
        assert snapshot_id == "binding_1"
        return SimpleNamespace(target_asset_use_snapshot_id="asset_use_1")

    def get_target_asset_use_snapshot(self, snapshot_id: str):
        assert snapshot_id == "asset_use_1"
        return SimpleNamespace(snapshot_artifact_id="artifact_asset_use")

    @contextmanager
    def open_artifact(self, artifact_id: str):
        self.open_counts[artifact_id] = self.open_counts.get(artifact_id, 0) + 1
        yield BytesIO(self.artifacts[artifact_id])

    @staticmethod
    def _region(
        region_id: str,
        ordinal: int,
        classification: str,
        min_x: float,
        min_y: float,
        max_x: float,
        max_y: float,
        area: float,
    ) -> RegionSummary:
        return RegionSummary(
            region_id=region_id,
            ordinal=ordinal,
            label=region_id,
            tags="",
            classification=classification,
            strategy_hint=None,
            footprint_area_xy=area,
            boundary_perimeter_xy=None,
            min_x=min_x,
            min_y=min_y,
            min_z=0,
            max_x=max_x,
            max_y=max_y,
            max_z=0,
            metrics_json="{}",
        )

    @staticmethod
    def _point(
        point_id: str,
        region_id: str,
        role_key: str,
        position: list[float],
    ) -> dict[str, object]:
        return {
            "id": point_id,
            "region_id": region_id,
            "role_key": role_key,
            "resolved_position": position,
        }

    @staticmethod
    def _assignment(point_id: str, asset_id: str, scale: float) -> dict[str, object]:
        return {
            "point_id": point_id,
            "asset_id": asset_id,
            "target_scale_xyz": [scale, scale, scale],
        }

    @staticmethod
    def _json_bytes(value: object) -> bytes:
        return json.dumps(value).encode("utf-8")


class _MultiBatchFixtureStore(_FixtureStore):
    def __init__(self) -> None:
        super().__init__()
        self.point_sets = {}
        self.assignment_sets = {}
        for index in range(3):
            point_set = replace(
                self.point_set,
                point_set_id=f"points_{index}",
                point_artifact_id=f"artifact_points_{index}",
            )
            assignment = replace(
                self.assignment_set,
                asset_assignment_set_id=f"assignments_{index}",
                point_set_id=point_set.point_set_id,
                assignment_artifact_id=f"artifact_assignments_{index}",
            )
            self.point_sets[point_set.point_set_id] = point_set
            self.assignment_sets[assignment.asset_assignment_set_id] = assignment
            self.artifacts[point_set.point_artifact_id] = self.artifacts["artifact_points"]
            self.artifacts[assignment.assignment_artifact_id] = self.artifacts["artifact_assignments"]

    def get_point_set(self, point_set_id: str):
        return self.point_sets[point_set_id]

    def get_assignment_set(self, assignment_set_id: str):
        return self.assignment_sets[assignment_set_id]

    def list_point_set_roles(self, point_set_id: str):
        return super().list_point_set_roles(self.point_set.point_set_id)

    def list_assignment_assets(self, assignment_set_id: str):
        return super().list_assignment_assets(self.assignment_set.asset_assignment_set_id)


def _complete_point_documents() -> _FixtureStore:
    from verdflow.planting import semantic_hash

    store = _FixtureStore()
    points = json.loads(store.artifacts["artifact_points"])
    points.update(
        coordinate_space="source_scene_world", offset_applied=False, kind="generated",
        resolution_policy="generated_position", point_count=4,
        strategy={"strategy_id": "fixture", "strategy_version": "1"},
    )
    for point in points["points"]:
        point.update(
            generated_position=point["resolved_position"], terrain_sample=None,
            rotation_quaternion_xyzw=[0, 0, 0, 1],
        )
    assigned = json.loads(store.artifacts["artifact_assignments"])
    assigned.update(
        point_count=4, target_type="dts", random_seed=7, resolver={"id": "fixture", "version": "1"},
        point_set_hash=semantic_hash(points), effective_binding_snapshot_hash="sha256:" + "0" * 64,
    )
    for point, mapping in zip(points["points"], assigned["assignments"], strict=True):
        mapping["role_key"] = point["role_key"]
    assigned["semantic_hash"] = semantic_hash(assigned)
    store.assignment_set = replace(store.assignment_set, semantic_hash=assigned["semantic_hash"])
    store.artifacts["artifact_points"] = store._json_bytes(points)
    store.artifacts["artifact_assignments"] = store._json_bytes(assigned)
    return store


class ProjectViewerQueriesTests(unittest.TestCase):
    def test_large_review_reuses_disk_index_and_preserves_viewport_order(self) -> None:
        store = _complete_point_documents()
        reference = ProjectViewerQueries(store)
        args = ("points_1", "assignments_1")
        view = dict(min_x=1, min_y=1, max_x=6, max_y=2, limit=1)
        expected_manifest = reference.point_manifest(*args)
        expected = reference.point_chunk(*args, **view)
        expected_second = reference.point_chunk(*args, **view, after_ordinal=expected.next_after_ordinal, assets=("oak",))
        store.open_counts.clear()

        with tempfile.TemporaryDirectory() as directory, patch(
            "verdflow.studio_service.viewer_queries._POINT_INDEX_MEMORY_ROWS", 0,
        ):
            root = Path(directory) / "cache"
            queries = ProjectViewerQueries(store, cache_root=root)
            try:
                self.assertEqual(queries.point_manifest(*args), expected_manifest)
                self.assertNotIn("artifact_points", store.open_counts)
                self.assertFalse(root.exists())
                self.assertEqual(queries.point_chunk(*args, **view), expected)
                self.assertEqual(
                    queries.point_chunk(*args, **view, after_ordinal=expected.next_after_ordinal, assets=("oak",)),
                    expected_second,
                )
                self.assertEqual(store.open_counts["artifact_points"], 1)
                self.assertEqual(store.open_counts["artifact_assignments"], 2)
                self.assertEqual(queries.point_manifest(*args), expected_manifest)
                index, = root.glob("point-review-*/*.sqlite")
                index.unlink()
                self.assertEqual(queries.point_chunk(*args, **view), expected)
                self.assertEqual(store.open_counts["artifact_points"], 2)
            finally:
                queries.close()
            self.assertEqual(list(root.iterdir()), [])

    def test_failed_large_projection_does_not_reuse_partial_index(self) -> None:
        for failure in ("tail", "closed"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory, patch(
                "verdflow.studio_service.viewer_queries._POINT_INDEX_MEMORY_ROWS", 0,
            ):
                store = _complete_point_documents()
                if failure == "tail":
                    store.artifacts["artifact_assignments"] += b" {}"
                root = Path(directory) / "cache"
                queries = ProjectViewerQueries(store, cache_root=root, cancelled=lambda: failure == "closed")
                try:
                    with self.assertRaises(ViewerProjectionError):
                        queries.point_chunk("points_1", "assignments_1", min_x=0, min_y=0, max_x=21, max_y=21)
                    self.assertEqual(list(root.glob("point-review-*/*.sqlite")), [])
                finally:
                    queries.close()

    def test_point_response_budget_keeps_following_items_addressable(self) -> None:
        queries = ProjectViewerQueries(_FixtureStore())
        args = ("points_1", "assignments_1")
        view = dict(min_x=0, min_y=0, max_x=8, max_y=4)
        with patch("verdflow.studio_service.point_review_index._RESPONSE_BYTES", 800):
            first = queries.point_chunk(*args, **view)
            self.assertTrue(first.truncated)
            self.assertEqual([item.ordinal for item in first.items], [0])
            second = queries.point_chunk(*args, **view, after_ordinal=first.next_after_ordinal)
            self.assertEqual([item.ordinal for item in second.items], [1])

    def test_three_batches_do_not_thrash_projection_caches(self) -> None:
        store = _MultiBatchFixtureStore()
        queries = ProjectViewerQueries(store)
        for index in range(3):
            queries.point_manifest(f"points_{index}", f"assignments_{index}")
        for _ in range(2):
            for index in range(3):
                chunk = queries.point_chunk(
                    f"points_{index}", f"assignments_{index}",
                    min_x=0, min_y=0, max_x=10, max_y=10,
                )
                self.assertEqual(len(chunk.items), 3)
        for index in range(3):
            self.assertEqual(store.open_counts[f"artifact_points_{index}"], 1)
            self.assertEqual(store.open_counts[f"artifact_assignments_{index}"], 1)

    def test_offscreen_batch_does_not_parse_whole_artifacts(self) -> None:
        store = _FixtureStore()
        chunk = ProjectViewerQueries(store).point_chunk(
            "points_1", "assignments_1",
            min_x=100, min_y=100, max_x=110, max_y=110,
        )
        self.assertEqual(chunk.items, ())
        self.assertFalse(chunk.truncated)
        self.assertEqual(store.open_counts, {})

    def test_weighted_lru_keeps_hot_entries_and_bounds_retained_rows(self) -> None:
        cache = _LruCache[list[int]](8, max_weight=6, weight=len)
        for key in ("a", "b", "c"):
            cache.put(key, [1, 2])
        self.assertIsNotNone(cache.get("a"))
        cache.put("d", [1, 2, 3])
        self.assertIsNone(cache.get("b"))
        self.assertIsNone(cache.get("c"))
        self.assertIsNotNone(cache.get("a"))
        cache.put("a", [1])
        cache.put("e", [1, 2])
        self.assertIsNotNone(cache.get("d"))
        cache.put("oversized", list(range(10)))
        self.assertIsNone(cache.get("d"))
        self.assertEqual(cache.get("oversized"), list(range(10)))

    def test_closed_region_rings_are_simplified_for_viewer_lod(self) -> None:
        ring = tuple(
            [(float(index), 0.0, 0.0) for index in range(101)]
            + [(100.0, float(index), 0.0) for index in range(1, 101)]
            + [(float(index), 100.0, 0.0) for index in range(99, -1, -1)]
            + [(0.0, float(index), 0.0) for index in range(99, -1, -1)]
        )

        simplified = _simplify_closed_ring(ring, 0.5)

        self.assertLessEqual(len(simplified), 5)
        self.assertGreaterEqual(len(simplified), 4)
        self.assertEqual(
            set(simplified),
            {
                (0.0, 0.0, 0.0),
                (100.0, 0.0, 0.0),
                (100.0, 100.0, 0.0),
                (0.0, 100.0, 0.0),
            },
        )

    def test_region_manifest_and_viewport_are_bounded_product_views(self) -> None:
        store = _FixtureStore()
        queries = ProjectViewerQueries(store)  # type: ignore[arg-type]

        manifest = queries.region_manifest("regions_1")
        first = queries.region_chunk(
            "regions_1",
            min_x=-1,
            min_y=-1,
            max_x=10,
            max_y=10,
            limit=1,
        )
        second = queries.region_chunk(
            "regions_1",
            min_x=-1,
            min_y=-1,
            max_x=10,
            max_y=10,
            after_ordinal=(
                first.next_after_ordinal
                if first.next_after_ordinal is not None
                else -1
            ),
            limit=1,
        )

        self.assertEqual(manifest.region_count, 3)
        self.assertEqual(manifest.bounds.max_x, 24)
        self.assertEqual(
            [(item.classification, item.region_count) for item in manifest.classifications],
            [("general_green", 2), ("street_tree", 1)],
        )
        self.assertTrue(first.truncated)
        self.assertEqual(first.next_after_ordinal, 0)
        self.assertEqual(first.items[0].region_id, "r1")
        self.assertFalse(second.truncated)
        self.assertEqual(second.items[0].region_id, "r2")
        self.assertEqual(store.open_counts["artifact_regions"], 1)

    def test_region_disk_index_reuses_exact_geometry_and_vertex_pages(self) -> None:
        store = _FixtureStore()
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("verdflow.studio_service.viewer_queries._REGION_INDEX_MEMORY_BYTES", 0),
            patch("verdflow.studio_service.viewer_queries._REGION_PAGE_VERTICES", 7),
        ):
            cache = Path(directory)
            queries = ProjectViewerQueries(store, cache_root=cache)  # type: ignore[arg-type]
            try:
                bounds = dict(min_x=-1, min_y=-1, max_x=25, max_y=25)
                after = -1
                ids = []
                for _ in range(3):
                    page = queries.region_chunk("regions_1", **bounds, after_ordinal=after)
                    ids.extend(item.region_id for item in page.items)
                    if page.next_after_ordinal is None:
                        break
                    after = page.next_after_ordinal
                self.assertEqual(ids, ["r1", "r2", "r3"])
                self.assertFalse(page.truncated)
                self.assertEqual(store.open_counts["artifact_regions"], 1)
                self.assertEqual(len(list(cache.rglob("*.sqlite"))), 1)
            finally:
                queries.close()
            self.assertEqual(list(cache.iterdir()), [])

    def test_region_disk_index_rejects_invalid_tail_without_retaining_partial_index(self) -> None:
        store = _FixtureStore()
        store.artifacts["artifact_regions"] += b"{}"
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("verdflow.studio_service.viewer_queries._REGION_INDEX_MEMORY_BYTES", 0),
        ):
            cache = Path(directory)
            queries = ProjectViewerQueries(store, cache_root=cache)  # type: ignore[arg-type]
            try:
                with self.assertRaisesRegex(ViewerProjectionError, "JSON"):
                    queries.region_chunk("regions_1", min_x=-1, min_y=-1, max_x=5, max_y=5)
                with self.assertRaisesRegex(ViewerProjectionError, "JSON"):
                    queries.region_overview("regions_1", min_x=-1, min_y=-1, max_x=5, max_y=5)
                self.assertEqual(list(cache.rglob("*.sqlite")), [])
            finally:
                queries.close()

    def test_dense_view_overview_covers_all_regions_and_retains_holes(self) -> None:
        import numpy

        store = _FixtureStore()
        original = json.loads(store.artifacts["artifact_regions"])
        original["regions"][0]["footprint_boundary_xyz"] = [
            [[0, 0, 0], [3, 0, 0], [3, 3, 0], [0, 3, 0], [0, 0, 0]],
            [[1, 1, 0], [2, 1, 0], [2, 2, 0], [1, 2, 0], [1, 1, 0]],
        ]
        store.artifacts["artifact_regions"] = store._json_bytes(original)
        queries = ProjectViewerQueries(store)  # type: ignore[arg-type]
        bounds = dict(min_x=-1, min_y=-1, max_x=25, max_y=25)
        hint = queries.region_chunk("regions_1", **bounds, overview_threshold=2)
        self.assertTrue(hint.overview_recommended)
        self.assertEqual(hint.items, ())
        self.assertNotIn("artifact_regions", store.open_counts)
        with patch("verdflow.studio_service.region_overview._BATCH_REGIONS", 1):
            overview = queries.region_overview("regions_1", **bounds)
        self.assertIs(queries.region_overview("regions_1", **bounds), overview)
        self.assertEqual(store.open_counts["artifact_regions"], 1)
        raster = numpy.frombuffer(zlib.decompress(b64decode(overview.pixels)), dtype="<u4").reshape(
            overview.height, overview.width,
        )

        def at(x: float, y: float) -> str | None:
            col = int((x - bounds["min_x"]) / (bounds["max_x"] - bounds["min_x"]) * overview.width)
            row = int((bounds["max_y"] - y) / (bounds["max_y"] - bounds["min_y"]) * overview.height)
            slot = raster[row, col]
            return overview.items[slot - 1].region_id if slot else None

        self.assertEqual({item.region_id for item in overview.items}, {"r1", "r2", "r3"})
        self.assertEqual((at(0.5, 0.5), at(1.5, 1.5), at(6, 0.5), at(22, 21)), ("r1", None, "r2", "r3"))
        street = queries.region_overview("regions_1", **bounds, classifications=("street_tree",))
        self.assertEqual([item.region_id for item in street.items], ["r2"])
        queries.close()
        for rows in (original["regions"], list(reversed(original["regions"]))):
            disk_store = _FixtureStore()
            disk_store.artifacts["artifact_regions"] = disk_store._json_bytes({**original, "regions": rows})
            with (
                tempfile.TemporaryDirectory() as directory,
                patch("verdflow.studio_service.viewer_queries._REGION_INDEX_MEMORY_BYTES", 0),
            ):
                disk_queries = ProjectViewerQueries(disk_store, cache_root=Path(directory))  # type: ignore[arg-type]
                try:
                    self.assertEqual(disk_queries.region_overview("regions_1", **bounds), overview)
                    disk_queries.region_chunk("regions_1", **bounds, limit=1)
                    self.assertEqual(disk_store.open_counts["artifact_regions"], 1)
                finally:
                    disk_queries.close()
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_overview_region_selection_uses_original_holes_and_skips_committed(self) -> None:
        store = _FixtureStore()
        document = json.loads(store.artifacts["artifact_regions"])
        outer = [
            [1.5 + 1.5 * math.cos(i * math.tau / 96), 1.5 + 1.5 * math.sin(i * math.tau / 96), 0]
            for i in range(96)
        ]
        hole = [[1, 1, 0], [2, 1, 0], [2, 2, 0], [1, 2, 0], [1, 1, 0]]
        document["regions"][0]["footprint_boundary_xyz"] = [outer + [outer[0]], hole]
        store.artifacts["artifact_regions"] = store._json_bytes(document)
        queries = ProjectViewerQueries(store)  # type: ignore[arg-type]
        with patch("verdflow.studio_service.viewer_queries._REGION_ITEM_VERTICES", 32):
            page = queries.region_chunk("regions_1", min_x=0, min_y=0, max_x=10, max_y=10, limit=1)
        self.assertEqual(page.items[0].boundary_mode, "overview")
        self.assertEqual(len(page.items[0].footprint_boundary_xyz), 2)
        self.assertTrue(page.truncated)
        self.assertEqual(queries.region_selection("regions_1", corners=((1.5, 1.5),)).region_ids, ())
        self.assertEqual(queries.region_selection("regions_1", corners=((6, 0.5),)).region_ids, ("r2",))
        self.assertEqual(queries.region_selection(
            "regions_1", corners=((1.1, 1.5), (1.5, 1.1), (1.9, 1.5), (1.5, 1.9)),
        ).region_ids, ())
        corners = ((-2.0, 0.0), (8.0, -2.0), (10.0, 5.0), (0.0, 7.0))
        with patch("verdflow.studio_service.viewer_queries._REGION_PAGE_BYTES", 32):
            first = queries.region_selection("regions_1", corners=corners)
            self.assertEqual((first.region_ids, first.next_after_ordinal), (("r1",), 0))
            second = queries.region_selection("regions_1", corners=corners, after_ordinal=0)
            self.assertEqual((second.region_ids, second.next_after_ordinal), (("r2",), None))
        store.occupied = ("r1",)
        self.assertEqual(queries.region_selection("regions_1", corners=corners).region_ids, ("r2",))

    def test_complex_region_near_view_reprojects_exact_rings_and_reuses_hit_geometry(self) -> None:
        import numpy

        store = _FixtureStore()
        document = json.loads(store.artifacts["artifact_regions"])
        document["regions"][0]["footprint_boundary_xyz"] = [
            [[0, 0, 0], [3, 0, 0], [3, 3, 0], [2.5, 3, 0], [2.5, 2.5, 0],
             [2, 2.5, 0], [2, 3, 0], [0, 3, 0], [0, 0, 0]],
            [[1, 1, 0], [1.1, 1, 0], [1.1, 1.1, 0], [1, 1.1, 0], [1, 1, 0]],
        ]
        store.artifacts["artifact_regions"] = store._json_bytes(document)
        queries = ProjectViewerQueries(store)  # type: ignore[arg-type]
        close = dict(min_x=0.8, min_y=0.8, max_x=1.4, max_y=1.4)
        with patch("verdflow.studio_service.viewer_queries._REGION_ITEM_VERTICES", 10):
            chunk = queries.region_chunk("regions_1", **close, overview_threshold=20_000)
            self.assertTrue(chunk.overview_recommended)
            self.assertEqual(chunk.items, ())
            detail = queries.region_overview("regions_1", **close)
            further = queries.region_overview("regions_1", min_x=-1, min_y=-1, max_x=25, max_y=25)
        self.assertLess(detail.bounds.max_x - detail.bounds.min_x, further.bounds.max_x - further.bounds.min_x)
        pixels = numpy.frombuffer(zlib.decompress(b64decode(detail.pixels)), dtype="<u4").reshape(detail.height, detail.width)
        col = int((1.05 - close["min_x"]) / (close["max_x"] - close["min_x"]) * detail.width)
        row = int((close["max_y"] - 1.05) / (close["max_y"] - close["min_y"]) * detail.height)
        self.assertEqual(pixels[row, col], 0)
        self.assertEqual(pixels[0, 0], 1)
        with patch("verdflow.studio_service.viewer_queries._exact_region_boundary", side_effect=AssertionError("reparsed exact rings")):
            self.assertEqual(queries.region_selection("regions_1", corners=((1.05, 1.05),)).region_ids, ())
            self.assertEqual(queries.region_selection("regions_1", corners=((1.3, 1.3),)).region_ids, ("r1",))
        queries.close()

    def test_point_review_joins_assignment_and_reuses_spatial_cache(self) -> None:
        store = _FixtureStore()
        queries = ProjectViewerQueries(store)  # type: ignore[arg-type]

        manifest = queries.point_manifest("points_1", "assignments_1")
        first = queries.point_chunk(
            "points_1",
            "assignments_1",
            min_x=0,
            min_y=0,
            max_x=10,
            max_y=10,
            roles=("tree",),
            limit=1,
        )
        second = queries.point_chunk(
            "points_1",
            "assignments_1",
            min_x=0,
            min_y=0,
            max_x=10,
            max_y=10,
            roles=("tree",),
            after_ordinal=(
                first.next_after_ordinal
                if first.next_after_ordinal is not None
                else -1
            ),
            limit=1,
        )

        self.assertEqual(manifest.target_type, "dts")
        self.assertEqual(manifest.region_set_id, "regions_1")
        self.assertEqual(
            [
                (
                    item.key,
                    item.display_name,
                    item.point_count,
                    item.reference_radius_m,
                    item.max_resolved_radius_m,
                )
                for item in manifest.assets
            ],
            [
                ("cornus", "白花山茱萸", 1, 1.5, 1.5),
                ("oak", "橡树", 3, 2.0, 2.4),
            ],
        )
        self.assertEqual(manifest.max_resolved_radius_m, 2.4)
        self.assertEqual([item.reference_height_m for item in manifest.assets], [3.0, 6.0])
        self.assertTrue(first.truncated)
        self.assertEqual(first.items[0].asset_id, "oak")
        self.assertEqual(first.items[0].target_scale_xyz, (0.8, 0.8, 0.8))
        self.assertFalse(second.truncated)
        self.assertEqual(second.items[0].point_id, "p3")
        self.assertEqual(store.open_counts["artifact_points"], 1)
        self.assertEqual(store.open_counts["artifact_assignments"], 1)
        self.assertEqual(store.open_counts["artifact_asset_use"], 1)


if __name__ == "__main__":
    unittest.main()
