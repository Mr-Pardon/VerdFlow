from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
for family in ("packages", "adapters", "apps"):
    sys.path[:0] = [str(path) for path in (ROOT / family).glob("*/src")]

from tests.unit import test_dts_adapter as dts_fixtures
from tests.unit import test_point_import as point_fixtures
from verdflow.dts_adapter import render_dts_xml
from verdflow.dts_adapter.xml import _render_persisted_dts_xml_composition
from verdflow.planting import (
    PlantingDocumentError, select_output_assets, select_persisted_output_assets,
    validate_elevation_point_set,
)


class PersistedOutputTests(unittest.TestCase):
    def test_elevation_validates_only_deltas_and_does_not_mutate_inputs(self):
        parent = point_fixtures.PointSetImportServiceTests._generated_points()
        sampled = point_fixtures.PointSetImportServiceTests._sampled_points()
        before = copy.deepcopy((parent, sampled))
        with patch("verdflow.planting.validation.validate_document", side_effect=AssertionError("full schema repeated")):
            self.assertIs(validate_elevation_point_set(sampled, parent, expected_point_count=2), sampled)
        self.assertEqual((parent, sampled), before)

    def test_elevation_still_rejects_changed_lineage_invalid_coordinates_and_bad_samples(self):
        parent = point_fixtures.PointSetImportServiceTests._generated_points()
        original = point_fixtures.PointSetImportServiceTests._sampled_points()
        mutations = [
            lambda d: d.update(point_count=3),
            lambda d: d.update(offset_applied=True),
            lambda d: d.update(coordinate_space="offset"),
            lambda d: d["strategy"].update(strategy_version="other"),
            lambda d: d["points"][0].update(id="other"),
            lambda d: d["points"][0].update(role_key="other"),
            lambda d: d["points"][0].update(rotation_quaternion_xyzw=[0, 0, 0, 0]),
            lambda d: d["points"][0].update(rotation_quaternion_xyzw=[0, 0, 0, True]),
            lambda d: d["points"][0].update(resolved_position=[1, 2, float("nan")]),
            lambda d: d["points"][0].update(resolved_position=[1, 2, True]),
            lambda d: d["points"][0].update(resolved_position=[1, 2, "3"]),
            lambda d: d["points"][0]["terrain_sample"].update(hit="yes"),
            lambda d: d["points"][0]["terrain_sample"].update(normal=[0, 0, 0]),
            lambda d: d["points"][0]["terrain_sample"].pop("target"),
            lambda d: d["points"][0]["terrain_sample"].update(face_index=True),
            lambda d: d["points"][0]["terrain_sample"].update(source_member_id=""),
            lambda d: d["points"][1].update(resolved_position=[1, 2, 99]),
            lambda d: d["points"].reverse(),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(case=index):
                sampled = copy.deepcopy(original)
                mutate(sampled)
                with self.assertRaises(PlantingDocumentError):
                    validate_elevation_point_set(sampled, parent, expected_point_count=2)

    def test_all_hit_selection_reuses_immutable_documents_without_rehash(self):
        points, _, assignments = dts_fixtures.DtsXmlAdapterTests._documents()
        with patch("verdflow.planting.selection.semantic_hash", side_effect=AssertionError("rehash")), patch(
            "verdflow.planting.validation.validate_document", side_effect=AssertionError("full schema")
        ):
            selected = select_persisted_output_assets(
                points, assignments, None, expected_point_count=1,
                expected_assignment_hash=assignments["semantic_hash"], require_terrain_hit=True,
            )
        self.assertIs(selected[0], points)
        self.assertIs(selected[1], assignments)

    def test_persisted_selection_checks_core_count_and_hash(self):
        points, _, assignments = dts_fixtures.DtsXmlAdapterTests._documents()
        for count, digest in [(2, assignments["semantic_hash"]), (1, "stale")]:
            with self.subTest(count=count, digest=digest), self.assertRaises(PlantingDocumentError):
                select_persisted_output_assets(points, assignments, None,
                    expected_point_count=count, expected_assignment_hash=digest)

    def test_filtered_output_and_fast_renderer_preserve_xml_bytes(self):
        points, binding, assignments = dts_fixtures.DtsXmlAdapterTests._documents()
        second = copy.deepcopy(points["points"][0])
        second.update(id="second", terrain_sample={"hit": False}, resolved_position=second["generated_position"])
        points["points"].append(second)
        points["point_count"] = 2
        assignments["assignments"].append({**assignments["assignments"][0], "point_id": "second"})
        assignments["point_count"] = 2
        from verdflow.planting import semantic_hash
        assignments["semantic_hash"] = semantic_hash({k: v for k, v in assignments.items() if k != "semantic_hash"})
        before = copy.deepcopy((points, assignments))
        expected = select_output_assets(points, assignments, None, require_terrain_hit=True)
        actual = select_persisted_output_assets(points, assignments, None,
            expected_point_count=2, expected_assignment_hash=assignments["semantic_hash"], require_terrain_hit=True)
        self.assertEqual(actual, expected)
        slow = render_dts_xml(expected[0], binding, expected[1], (10, 20, 30))
        fast = _render_persisted_dts_xml_composition(((actual[0], binding, actual[1]),), (10, 20, 30))
        self.assertEqual(fast, slow)
        self.assertEqual((points, assignments), before)

    def test_renderer_still_checks_finite_coordinates_and_assignment_coverage(self):
        for field, value in [("resolved_position", [1, 2, float("inf")]), ("rotation_quaternion_xyzw", [0, 0, 0, 0])]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                points, binding, assignments = dts_fixtures.DtsXmlAdapterTests._documents()
                points["points"][0][field] = value
                _render_persisted_dts_xml_composition(((points, binding, assignments),), (0, 0, 0))
        points, binding, assignments = dts_fixtures.DtsXmlAdapterTests._documents()
        assignments["assignments"][0]["point_id"] = "other"
        with self.assertRaisesRegex(ValueError, "exactly one assignment"):
            _render_persisted_dts_xml_composition(((points, binding, assignments),), (0, 0, 0))

    def test_raw_document_renderer_retains_strict_validation(self):
        points, binding, assignments = dts_fixtures.DtsXmlAdapterTests._documents()
        points["points"][0]["id"] = ""
        with self.assertRaises(ValueError):
            render_dts_xml(points, binding, assignments, (0, 0, 0))

    def test_renderer_rejects_non_positive_scale(self):
        points, binding, assignments = dts_fixtures.DtsXmlAdapterTests._documents()
        assignments["assignments"][0]["target_scale_xyz"] = [1, 0, 1]
        with self.assertRaisesRegex(ValueError, "positive"):
            _render_persisted_dts_xml_composition(((points, binding, assignments),), (0, 0, 0))


class DeliveryWaitingTests(unittest.TestCase):
    def test_runtime_cancels_receipt_wait_before_joining_owner_thread(self):
        from verdflow.system_host.dts_live_runtime import DtsLiveTemporaryRuntime
        port = Mock()
        with patch("verdflow.system_host.dts_live_runtime.ProjectRuntime") as runtime_type:
            runtime = DtsLiveTemporaryRuntime(connector_session_id="test", project_path=Path("unused"),
                library_path=Path("unused-library"), port=port, idle_cycle_seconds=0.1)
            order = Mock()
            order.attach_mock(port.cancel, "cancel")
            order.attach_mock(runtime_type.return_value.close, "close")
            runtime.close(1.0, remove_files=False)
        self.assertEqual([call[0] for call in order.mock_calls], ["cancel", "close"])

    def test_default_runtime_has_no_import_deadline(self):
        from verdflow.system_host.dts_live_runtime import DtsLiveTemporaryRuntime
        from pathlib import Path
        with patch("verdflow.system_host.dts_live_runtime.ProjectRuntime"):
            runtime = DtsLiveTemporaryRuntime(connector_session_id="test", project_path=Path("unused"),
                library_path=Path("unused-library"), port=Mock(), idle_cycle_seconds=0.1)
        self.assertIsNone(runtime.delivery_timeout_seconds)

    def test_receipt_wait_is_bounded_per_poll_but_does_not_expire_at_five_minutes(self):
        from verdflow.system_host.dts_live_runtime import _wait_for_delivery
        port = Mock()
        receipt = object()
        port.wait_for_delivery.side_effect = [None, None, receipt]
        with patch("verdflow.system_host.dts_live_runtime.time.monotonic", side_effect=AssertionError("implicit deadline")):
            self.assertIs(_wait_for_delivery(port, Mock()), receipt)
        self.assertTrue(all(call.kwargs["timeout_seconds"] == 1 for call in port.wait_for_delivery.call_args_list))

    def test_cancelled_or_closed_session_immediately_ends_receipt_wait(self):
        from verdflow.system_host.dts_live_runtime import _wait_for_delivery
        port = Mock()
        port.wait_for_delivery.side_effect = [None, ValueError("delivery was cancelled")]
        with self.assertRaisesRegex(ValueError, "cancelled"):
            _wait_for_delivery(port, Mock())
