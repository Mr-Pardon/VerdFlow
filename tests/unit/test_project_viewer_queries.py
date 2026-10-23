from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from io import BytesIO
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest


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


class ProjectViewerQueriesTests(unittest.TestCase):
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
