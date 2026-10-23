from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
    str(ROOT / "packages" / "planting" / "src"),
    str(ROOT / "packages" / "core" / "src"),
    str(ROOT / "adapters" / "dts" / "src"),
]

from verdflow.dts_adapter import (  # noqa: E402
    DtsLiveCommandKind,
    DtsLiveElevationBatch,
    DtsLiveExecutionSnapshot,
    DtsLiveOperationStage,
    DtsLiveSession,
)


class DtsLiveSessionTests(unittest.TestCase):
    def test_chunked_elevation_excludes_whole_failed_regions_and_validates_ids(self) -> None:
        session = DtsLiveSession()
        selection = session.begin_selection(_execution_snapshot(), [
            {"region_id": name, "name": name, "coordinates": [[x, 0, 0], [x + 1, 0, 0], [x, 1, 0]]}
            for name, x in (("a", 0), ("b", 10))
        ])
        command = session.request_elevation(
            operation_id=selection.correlation.operation_id,
            generation_token=selection.correlation.generation_token,
            point_set_id="p", point_ids=["a1", "a2", "b1"],
            lines=[{"start": [i, 0, 100], "end": [i, 0, -100]} for i in range(3)],
            batches=[DtsLiveElevationBatch("a", "A", selection.regions[0].bbox, 0, 2),
                     DtsLiveElevationBatch("b", "B", selection.regions[1].bbox, 2, 1)],
        )
        hit = {"LineIntersectPoint": [0, 0, 12]}
        for indices in ([True], [-1], [3], [1, 1]):
            with self.assertRaises(ValueError):
                session.accept_elevation_chunk(command.correlation, indices, [hit] * len(indices))
        session.accept_elevation_chunk(command.correlation, [2, 0], [hit, hit])
        session.accept_elevation_chunk(command.correlation, [2, 0], [hit, hit])
        with self.assertRaisesRegex(ValueError, "conflicts"):
            session.accept_elevation_chunk(command.correlation, [2], [{"LineIntersectPoint": [1, 1, 12]}])
        with self.assertRaisesRegex(ValueError, "every point"):
            session.accept_elevation(command.correlation, None, chunked=True)
        with self.assertRaisesRegex(ValueError, "Unknown"):
            session.accept_elevation(command.correlation, None, chunked=True,
                                     failed_regions=[{"region_id": "unknown", "reason": "miss"}])
        samples = session.accept_elevation(command.correlation, None, chunked=True,
                                           failed_regions=[{"region_id": "a", "reason": "miss"}])
        self.assertEqual([s.point_id for s in samples], ["a1", "a2", "b1"])
        self.assertEqual([s.position for s in samples], [None, None, (0, 0, 12)])
        self.assertEqual(session.snapshot().sampling_failures[0].region_id, "a")
        with self.assertRaises(ValueError):
            session.accept_elevation_chunk(command.correlation, [2], [hit])

    def test_region_collection_is_frozen_and_reused(self) -> None:
        session = DtsLiveSession("multi")
        regions = [
            {"region_id": "a", "name": "A", "coordinates": [[0, 0, 2], [10, 0, 3], [0, 10, 2]]},
            {"region_id": "b", "name": "B", "coordinates": [[100, 100, 4], [110, 100, 4], [100, 110, 4]]},
        ]
        selection = session.begin_selection(_execution_snapshot(), regions)
        regions[0]["coordinates"][0][0] = 999
        self.assertEqual(selection.regions[0].bbox, (0, 0, 2, 10, 10, 3))
        self.assertEqual(session.snapshot().selection_coordinate_count, 6)
        self.assertEqual(len(session.snapshot().selection_regions), 2)
        self.assertEqual(session.commands_after(0), ())
        session.cancel_current()
        self.assertEqual(session.begin_regeneration(_execution_snapshot()).regions, selection.regions)

    def test_invalid_regions_do_not_replace_previous_selection(self) -> None:
        session = DtsLiveSession("multi")
        valid = {"region_id": "a", "name": "A", "coordinates": [[0, 0, 0], [1, 0, 0], [0, 1, 0]]}
        session.begin_selection(_execution_snapshot(), [valid])
        before = session.snapshot()
        for regions in ([], [valid, valid], [{**valid, "coordinates": [[0, 0, 0], [1, 0, 0], [2, 0, 0]]}]):
            with self.assertRaises(ValueError):
                session.begin_selection(_execution_snapshot(), regions)
            self.assertEqual(session.snapshot(), before)

    def test_elevation_batches_require_complete_partition(self) -> None:
        session = DtsLiveSession()
        selection = session.begin_selection(_execution_snapshot(), [{
            "region_id": "a", "name": "A", "coordinates": [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
        }])
        region = selection.regions[0]
        with self.assertRaisesRegex(ValueError, "partition"):
            session.request_elevation(
                operation_id=selection.correlation.operation_id,
                generation_token=selection.correlation.generation_token,
                point_set_id="p", point_ids=["a"], lines=[{"start": [0, 0, 1], "end": [0, 0, -1]}],
                batches=[DtsLiveElevationBatch("a", "A", region.bbox, 1, 1)],
            )
        self.assertEqual(session.snapshot().stage, DtsLiveOperationStage.SELECTION_READY)

    def test_single_region_elevation_and_delivery_close_by_exact_ids(self) -> None:
        session = DtsLiveSession("connector-session")
        selection_command = session.begin_selection(_execution_snapshot())

        self.assertEqual(
            selection_command.kind,
            DtsLiveCommandKind.BEGIN_POLYGON_SELECTION,
        )
        selection = session.accept_selection(
            selection_command.correlation,
            [
                [0.0, 0.0, 5.0],
                [10.0, 0.0, 5.0],
                [10.0, 10.0, 5.0],
                [0.0, 0.0, 5.0],
            ],
        )
        self.assertEqual(len(selection.regions[0].coordinates), 3)
        self.assertEqual(
            session.snapshot().stage,
            DtsLiveOperationStage.SELECTION_READY,
        )

        elevation_command = session.request_elevation(
            operation_id=selection.correlation.operation_id,
            generation_token=selection.correlation.generation_token,
            point_set_id="points_generated",
            point_ids=("point_a", "point_b"),
            lines=(
                {"start": [1.0, 1.0, 100.0], "end": [1.0, 1.0, -100.0]},
                {"start": [2.0, 2.0, 100.0], "end": [2.0, 2.0, -100.0]},
            ),
        )
        samples = session.accept_elevation(
            elevation_command.correlation,
            {
                "result": 0,
                "intersects": [
                    [{"LineIntersectPoint": [1.0, 1.0, 12.0]}],
                    [{"LineIntersectPoint": [2.0, 2.0, 13.0]}],
                ],
            },
        )
        self.assertEqual(
            [(sample.point_id, sample.position[2]) for sample in samples],
            [("point_a", 12.0), ("point_b", 13.0)],
        )
        self.assertEqual(
            session.wait_for_elevation(
                elevation_command.correlation,
                timeout_seconds=0.0,
            ),
            samples,
        )

        delivery_command = session.request_delivery(
            operation_id=selection.correlation.operation_id,
            generation_token=selection.correlation.generation_token,
            export_id="export_current",
            xml_path=r"D:\runtime\current.xml",
        )
        with self.assertRaisesRegex(ValueError, "differs"):
            session.accept_delivery(
                delivery_command.correlation,
                export_id="export_other",
                succeeded=True,
            )
        receipt = session.accept_delivery(
            delivery_command.correlation,
            export_id="export_current",
            succeeded=True,
        )
        self.assertTrue(receipt.succeeded)
        self.assertEqual(
            session.wait_for_delivery(
                delivery_command.correlation,
                timeout_seconds=0.0,
            ),
            receipt,
        )
        self.assertEqual(
            session.snapshot().stage,
            DtsLiveOperationStage.COMPLETED,
        )
        self.assertEqual(
            [item.kind for item in session.commands_after(0)],
            [
                DtsLiveCommandKind.BEGIN_POLYGON_SELECTION,
                DtsLiveCommandKind.SAMPLE_ELEVATION,
                DtsLiveCommandKind.IMPORT_XML,
            ],
        )

    def test_new_generation_rejects_late_selection_callback(self) -> None:
        session = DtsLiveSession("connector-session")
        first = session.begin_selection(_execution_snapshot())
        second = session.begin_selection(_execution_snapshot())

        with self.assertRaisesRegex(ValueError, "operation is stale"):
            session.accept_selection(
                first.correlation,
                [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
            )
        accepted = session.accept_selection(
            second.correlation,
            [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
        )
        self.assertEqual(accepted.correlation.generation_token, 2)

    def test_regeneration_reuses_last_selection_without_drawing_command(self) -> None:
        session = DtsLiveSession("connector-session")
        command = session.begin_selection(_execution_snapshot())
        accepted = session.accept_selection(
            command.correlation,
            [[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 0, 0]],
        )
        session.cancel_current()

        regenerated = session.begin_regeneration(_execution_snapshot())

        self.assertEqual(regenerated.regions, accepted.regions)
        self.assertEqual(regenerated.correlation.generation_token, 2)
        self.assertNotEqual(
            regenerated.correlation.operation_id,
            accepted.correlation.operation_id,
        )
        self.assertEqual(
            session.snapshot().stage,
            DtsLiveOperationStage.SELECTION_READY,
        )
        self.assertEqual(
            [item.kind for item in session.commands_after(0)],
            [DtsLiveCommandKind.BEGIN_POLYGON_SELECTION],
        )

    def test_regeneration_requires_a_previous_terminal_selection(self) -> None:
        session = DtsLiveSession("connector-session")
        with self.assertRaisesRegex(ValueError, "previous selection"):
            session.begin_regeneration(_execution_snapshot())

        command = session.begin_selection(_execution_snapshot())
        session.accept_selection(
            command.correlation,
            [[0, 0, 0], [10, 0, 0], [0, 10, 0]],
        )
        with self.assertRaisesRegex(ValueError, "must finish"):
            session.begin_regeneration(_execution_snapshot())

    def test_elevation_requires_all_hits_and_exact_point_count(self) -> None:
        session = DtsLiveSession()
        selection = session.begin_selection(_execution_snapshot())
        accepted = session.accept_selection(
            selection.correlation,
            [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
        )
        elevation = session.request_elevation(
            operation_id=accepted.correlation.operation_id,
            generation_token=accepted.correlation.generation_token,
            point_set_id="points_generated",
            point_ids=("point_a", "point_b"),
            lines=(
                {"start": [0, 0, 1], "end": [0, 0, -1]},
                {"start": [1, 1, 1], "end": [1, 1, -1]},
            ),
        )

        with self.assertRaisesRegex(ValueError, "every point"):
            session.accept_elevation(
                elevation.correlation,
                {
                    "intersects": [
                        [{"LineIntersectPoint": [0, 0, 0]}],
                        [],
                    ]
                },
            )
        self.assertEqual(
            session.snapshot().stage,
            DtsLiveOperationStage.ELEVATION_PENDING,
        )

    def test_invalid_execution_snapshot_cannot_enable_selection(self) -> None:
        invalid = _execution_snapshot(target_type="blender")
        with self.assertRaisesRegex(ValueError, "must target dts"):
            DtsLiveSession().begin_selection(invalid)


def _execution_snapshot(
    *,
    target_type: str = "dts",
) -> DtsLiveExecutionSnapshot:
    return DtsLiveExecutionSnapshot(
        strategy_package_hash="sha256:" + "1" * 64,
        strategy_id="universal_natural_grove",
        strategy_version="2.0.0",
        profile_id="dts_default",
        profile_semantic_hash="sha256:" + "2" * 64,
        target_type=target_type,
        asset_source_kind="profile",
        asset_source_id="dts_default",
        asset_source_generation=None,
        asset_source_hash="sha256:" + "2" * 64,
        effective_binding_semantic_hash="sha256:" + "4" * 64,
    )


if __name__ == "__main__":
    unittest.main()
