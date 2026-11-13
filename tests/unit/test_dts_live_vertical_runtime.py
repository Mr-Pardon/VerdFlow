from __future__ import annotations

from pathlib import Path
from contextlib import closing
import io
import json
import sqlite3
import math
import xml.etree.ElementTree as ET
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


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
    str(ROOT / "apps" / "dts-live-connector" / "src"),
]

from verdflow.dts_adapter import (  # noqa: E402
    DtsLiveCommandKind,
    DtsLiveOperationStage,
)
from verdflow.dts_adapter.live_worker import _write_elevation_json  # noqa: E402
from verdflow.dts_live_connector import DtsLiveConnectorApplication  # noqa: E402
from verdflow.library_store import LibraryStore  # noqa: E402
from verdflow.planting import (  # noqa: E402
    build_profile_v2,
    catalog_binding_candidate,
    load_catalog_yaml,
    load_profile_yaml,
    prepare_asset_catalog_document,
    prepare_role_binding_profile_document,
)
from verdflow.project_store import ProjectStorageProfile  # noqa: E402
from verdflow.strategy_worker import inspect_strategy_package  # noqa: E402
from verdflow.system_host.dts_live_runtime import (  # noqa: E402
    DtsLiveTemporaryRuntime,
)
from verdflow.system_host import (  # noqa: E402
    DtsLiveConfigurationRequest,
    SystemHost,
    SystemHostConfig,
)


STRATEGY_ROOT = ROOT / "strategy-packs" / "universal-natural-grove"
ASSET_ROOT = ROOT / "asset-packs" / "dts-default"


class DtsLiveVerticalRuntimeTests(unittest.TestCase):
    def test_temporary_runtime_explicitly_uses_ephemeral_storage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch(
            "verdflow.system_host.dts_live_runtime.ProjectRuntime"
        ) as runtime_type:
            root = Path(temporary)
            DtsLiveTemporaryRuntime(
                connector_session_id="session-test",
                project_path=root / "session.verdflow",
                library_path=root / "library",
                port=object(),  # type: ignore[arg-type]
                idle_cycle_seconds=0.1,
            )

            config = runtime_type.call_args.args[0]
            self.assertIs(
                config.project_storage_profile,
                ProjectStorageProfile.EPHEMERAL,
            )

    def test_application_creates_launch_and_cleans_temporary_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = SystemHostConfig(
                library_path=root / "library",
                system_path=root / "system",
                project_start_timeout_seconds=60.0,
                project_call_timeout_seconds=60.0,
            )
            with SystemHost(config) as system:
                with DtsLiveConnectorApplication(system) as application:
                    launch = application.create_launch()
                    runtime_path = (
                        root
                        / "system"
                        / "runtime"
                        / "dts-live"
                        / f"{launch.connector_session_id}.verdflow"
                    )
                    self.assertTrue(runtime_path.exists())
                    self.assertTrue(launch.url.startswith(application.base_url))
                    self.assertIn("#session=", launch.url)
                    self.assertIn("&bootstrap=", launch.url)
                self.assertFalse(runtime_path.exists())

    def test_single_region_runs_through_export_and_delivery(self) -> None:
        self._run_regions(multi=False)

    def test_paired_open_paths_use_shared_generation_assignment_elevation_and_xml(
        self,
    ) -> None:
        self._run_regions(multi=True, path_input=True)

    def test_failed_path_is_excluded_without_discarding_paired_roles_on_other_path(
        self,
    ) -> None:
        self._run_regions(multi=True, path_input=True, partial=True)

    def test_overlapping_paths_do_not_share_failures_and_write_staged_elevation(self) -> None:
        with patch("verdflow.dts_adapter.live_worker._ELEVATION_STREAM_ROWS", 0):
            self._run_regions(multi=True, path_input=True, partial=True, overlapping=True)

    def test_staged_elevation_preserves_compact_pointset_bytes_across_batches(self) -> None:
        for version in ("2", "3"):
            with self.subTest(version=version):
                document = {
                    "schema": "verdflow.points", "version": version, "kind": "elevation_resolved",
                    "points": [{"id": f"e\u0301:{index}", "generated_position": [0.0, -0.0, 1e-20]}
                               for index in range(2050)],
                    "resolution_policy": "sample_hit_else_generated",
                }
                stream = io.BytesIO()
                checkpoints = []
                _write_elevation_json(stream, document, lambda: checkpoints.append(stream.tell()))
                self.assertEqual(stream.getvalue(), json.dumps(
                    document, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
                ).encode("utf-8"))
                self.assertEqual(len(checkpoints), 3)

    def test_all_failed_paths_create_no_xml(self) -> None:
        self._run_regions(multi=True, path_input=True, all_failed=True)

    def test_multiple_regions_use_one_generation_sampling_and_export(self) -> None:
        self._run_regions(multi=True)

    def test_legal_hole_survives_standard_regions_and_generates_no_points_inside(self) -> None:
        self._run_regions(multi=True, with_hole=True)

    def test_tweaked_plan_runs_without_original_catalog(self) -> None:
        self._run_regions(multi=True, tweaked=True)

    def test_unsaved_multi_catalog_mapping_runs_through_xml_without_sources(self) -> None:
        self._run_regions(multi=True, multi_catalog=True)

    def test_failed_region_is_excluded_without_losing_successful_xml(self) -> None:
        self._run_regions(multi=True, partial=True)

    def test_all_failed_regions_do_not_create_xml(self) -> None:
        self._run_regions(multi=True, all_failed=True)

    def test_session_shutdown_while_waiting_for_native_import_does_not_hang(self) -> None:
        self._run_regions(multi=False, close_pending=True)

    def _run_regions(
        self,
        *,
        multi: bool,
        partial: bool = False,
        all_failed: bool = False,
        tweaked: bool = False,
        close_pending: bool = False,
        multi_catalog: bool = False,
        with_hole: bool = False,
        path_input: bool = False,
        overlapping: bool = False,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package_hash, profile_id = _prepare_library(
                root / "library",
                path_input=path_input,
            )
            config = SystemHostConfig(
                library_path=root / "library",
                system_path=root / "system",
                project_start_timeout_seconds=60.0,
                project_call_timeout_seconds=60.0,
            )
            with SystemHost(config) as system:
                assignments = {}
                if tweaked:
                    profile = system.get_role_binding_profile(profile_id)
                    assignments = profile.assignments
                    assignments[next(iter(assignments))]["enabled"] = False
                    system.delete_asset_catalog("dts", "dts_default")
                if multi_catalog:
                    profile = system.get_role_binding_profile(profile_id)
                    assignments = profile.assignments
                    for index, assignment in enumerate(assignments.values()):
                        identifier = "permanent" if index % 2 == 0 else "temporary"
                        asset = {"asset_id": "tree_01", "display_name": identifier,
                                 "provider_metadata": {"model_name": f"Model_{identifier}"}}
                        source = {"catalog_id": identifier, "target_type": "dts", "provider_id": "dts.static-mesh"}
                        assignment["candidates"] = [catalog_binding_candidate(source, asset)]
                    system.delete_asset_catalog("dts", "dts_default")
                bootstrap = system.create_dts_live_session()
                runtime_path = (
                    root
                    / "system"
                    / "runtime"
                    / "dts-live"
                    / f"{bootstrap.connector_session_id}.verdflow"
                )
                self.assertTrue(runtime_path.exists())
                manager = system.dts_live_connector
                grant = manager.exchange_bootstrap(
                    bootstrap.connector_session_id,
                    bootstrap.bootstrap_nonce,
                )
                selection_command = manager.begin_selection(
                    bootstrap.connector_session_id,
                    grant.access_token,
                    DtsLiveConfigurationRequest(
                        strategy_package_hash=package_hash,
                        profile_id=None if multi_catalog else profile_id,
                        assignments=assignments,
                    ),
                    regions=(
                        [
                            {
                                "region_id": "a",
                                "name": "Area A",
                                "coordinates": [
                                    [0, 0, 0],
                                    [100, 0, 0],
                                    [100, 100, 0],
                                    [0, 100, 0],
                                ],
                                "holes": (
                                    [
                                        [
                                            [20, 20, 0],
                                            [80, 20, 0],
                                            [80, 80, 0],
                                            [20, 80, 0],
                                        ]
                                    ]
                                    if with_hole
                                    else []
                                ),
                            },
                            {
                                "region_id": "b",
                                "name": "Area B",
                                "coordinates": [
                                    [1000, 1000, 0],
                                    [1100, 1000, 0],
                                    [1100, 1100, 0],
                                    [1000, 1100, 0],
                                ],
                            },
                        ]
                        if multi and not path_input
                        else None
                    ),
                    paths=(
                        [
                            {
                                "path_id": "route-a",
                                "name": "Route A",
                                "coordinates": [[0, 0, 0], [100, 0, 0], [100, 100, 0]],
                            },
                            {
                                "path_id": "route-b",
                                "name": "Route B",
                                "coordinates": ([[0, 0, 0], [100, 0, 0], [100, 100, 0]] if overlapping
                                                else [[1000, 1000, 0], [1060, 1080, 0]]),
                            },
                        ]
                        if path_input
                        else None
                    ),
                )
                selection = selection_command if multi else manager.accept_selection(
                    bootstrap.connector_session_id,
                    grant.access_token,
                    selection_command.correlation,
                    [
                        [0.0, 0.0, 0.0],
                        [100.0, 0.0, 0.0],
                        [100.0, 100.0, 0.0],
                        [0.0, 100.0, 0.0],
                    ],
                )
                system.submit_dts_live_selection(selection)

                sequence = 0 if multi else selection_command.sequence
                export_id = None
                elevation_count = 0
                expected_xml_count = None
                deadline = time.monotonic() + 30.0
                while time.monotonic() < deadline and export_id is None:
                    commands = manager.commands_after(
                        bootstrap.connector_session_id,
                        grant.access_token,
                        sequence,
                        timeout_seconds=1.0,
                    )
                    for command in commands:
                        sequence = command.sequence
                        if command.kind is DtsLiveCommandKind.SAMPLE_ELEVATION:
                            elevation_count += 1
                            lines = command.payload["lines"]
                            batches = command.payload["batches"]
                            self.assertEqual(len(batches), 2 if multi else 1)
                            self.assertEqual(sum(batch["count"] for batch in batches), len(lines))
                            if path_input:
                                expected_xml_count = len(lines) * 2
                                if overlapping:
                                    self.assertEqual(batches[0]["count"], batches[1]["count"])
                                    first_count = batches[0]["count"]
                                    self.assertEqual(lines[:first_count], lines[first_count:])
                            if with_hole:
                                first = batches[0]
                                self.assertGreater(first["count"], 0)
                                self.assertFalse(any(20 < line["start"][0] < 80 and 20 < line["start"][1] < 80
                                                     for line in lines[first["start"]:first["start"] + first["count"]]))
                            for batch in batches:
                                batch_lines = lines[batch["start"]:batch["start"] + batch["count"]]
                                if path_input:
                                    self.assertEqual(len({tuple(line["start"]) for line in batch_lines}), len(batch_lines))
                                for line in batch_lines:
                                    self.assertGreaterEqual(line["start"][0], batch["bbox"][0])
                                    self.assertLessEqual(line["start"][0], batch["bbox"][3])
                            if all_failed:
                                manager.accept_elevation(
                                    bootstrap.connector_session_id,
                                    grant.access_token,
                                    command.correlation,
                                    None,
                                    chunked=True,
                                    failed_regions=[
                                        {
                                            (
                                                "path_id" if path_input else "region_id"
                                            ): batch[
                                                "path_id" if path_input else "region_id"
                                            ],
                                            "reason": "test terrain miss",
                                        }
                                        for batch in batches
                                    ],
                                )
                                continue
                            if partial:
                                failed, good = batches
                                indices = list(range(good["start"], good["start"] + good["count"]))
                                expected_xml_count = good["count"] * (2 if path_input else 1)
                                # An earlier successful window of the failed face must also be excluded.
                                indices.append(failed["start"])
                                for start in range(0, len(indices), 2000):
                                    chunk = indices[start:start + 2000]
                                    manager.accept_elevation_chunk(
                                        bootstrap.connector_session_id,
                                        grant.access_token,
                                        command.correlation,
                                        chunk,
                                        {"intersects": [[{"LineIntersectPoint": [lines[index]["start"][0], lines[index]["start"][1], 0.0]}] for index in chunk]},
                                    )
                                manager.accept_elevation(
                                    bootstrap.connector_session_id,
                                    grant.access_token,
                                    command.correlation,
                                    None,
                                    chunked=True,
                                    failed_regions=[
                                        {
                                            (
                                                "path_id" if path_input else "region_id"
                                            ): failed[
                                                "path_id" if path_input else "region_id"
                                            ],
                                            "reason": "test terrain miss",
                                        }
                                    ],
                                )
                                continue
                            manager.accept_elevation(
                                bootstrap.connector_session_id,
                                grant.access_token,
                                command.correlation,
                                {
                                    "intersects": [
                                        [
                                            {
                                                "LineIntersectPoint": [
                                                    line["start"][0],
                                                    line["start"][1],
                                                    0.0,
                                                ]
                                            }
                                        ]
                                        for line in lines
                                    ]
                                },
                            )
                        elif command.kind is DtsLiveCommandKind.IMPORT_XML:
                            export_id = command.payload["export_id"]
                            xml_path = Path(command.payload["xml_path"])
                            self.assertTrue(xml_path.is_file())
                            self.assertIn("ModelPoint", xml_path.read_text("utf-8"))
                            if path_input:
                                with closing(
                                    sqlite3.connect(runtime_path / "project.db")
                                ) as db:
                                    self.assertEqual(
                                        db.execute(
                                            "SELECT COUNT(*) FROM path_sets"
                                        ).fetchone()[0],
                                        1,
                                    )
                                    self.assertEqual(
                                        db.execute(
                                            "SELECT COUNT(*) FROM region_sets"
                                        ).fetchone()[0],
                                        0,
                                    )
                                    run = db.execute(
                                        "SELECT region_set_id, path_set_id FROM planting_runs"
                                    ).fetchone()
                                    self.assertIsNone(run[0])
                                    self.assertIsNotNone(run[1])
                                xml = xml_path.read_text("utf-8")
                                self.assertIn("Fixture_Tree", xml)
                                self.assertIn("Fixture_Planter", xml)
                                self.assertEqual(
                                    xml.count("<ModelName>Fixture_Tree</ModelName>"),
                                    xml.count("<ModelName>Fixture_Planter</ModelName>"),
                                )
                                vegetation_scales = []
                                for row in ET.fromstring(xml):
                                    self.assertEqual(float(row.findtext("LocationZ")), 0.0)
                                    matrix = [
                                        float(value)
                                        for value in row.findtext("Matrix3").split(",")
                                    ]
                                    scale = math.sqrt(
                                        sum(value ** 2 for value in matrix[:3])
                                    )
                                    if row.findtext("ModelName") == "Fixture_Planter":
                                        self.assertAlmostEqual(scale, 1.0, places=5)
                                        y = float(row.findtext("LocationY"))
                                        self.assertEqual(
                                            tuple(matrix[3:6]),
                                            (0.6, 0.8, 0) if y >= 1000
                                            else (1, 0, 0) if y == 0 else (0, 1, 0),
                                        )
                                    else:
                                        self.assertTrue(1.0 <= scale <= 2.0)
                                        vegetation_scales.append(scale)
                                self.assertGreater(len(set(vegetation_scales)), 1)
                            if multi_catalog:
                                xml = xml_path.read_text("utf-8")
                                self.assertIn("Model_permanent", xml)
                                self.assertIn("Model_temporary", xml)
                            if close_pending:
                                system.close_dts_live_session(bootstrap.connector_session_id)
                                self.assertEqual(
                                    manager.snapshot(bootstrap.connector_session_id, grant.access_token).stage,
                                    DtsLiveOperationStage.CANCELLED,
                                )
                                self.assertFalse(runtime_path.exists())
                                return
                            if expected_xml_count is not None:
                                self.assertEqual(xml_path.read_text("utf-8").count("<ModelPoint>"), expected_xml_count)
                            if partial:
                                failures = manager.snapshot(bootstrap.connector_session_id, grant.access_token).sampling_failures
                                self.assertEqual(
                                    [
                                        (
                                            failure.path_id
                                            if path_input
                                            else failure.region_id
                                        )
                                        for failure in failures
                                    ],
                                    ["route-a" if path_input else "a"],
                                )
                            manager.accept_delivery(
                                bootstrap.connector_session_id,
                                grant.access_token,
                                command.correlation,
                                export_id=export_id,
                                succeeded=True,
                            )
                    if all_failed and manager.snapshot(bootstrap.connector_session_id, grant.access_token).stage is DtsLiveOperationStage.FAILED:
                        break

                if all_failed:
                    self.assertIsNone(export_id)
                    result = manager.snapshot(bootstrap.connector_session_id, grant.access_token)
                    self.assertEqual(result.stage, DtsLiveOperationStage.FAILED)
                    self.assertEqual(len(result.sampling_failures), 2)
                    self.assertIn("no XML", result.error)
                    return
                self.assertIsNotNone(export_id, manager.snapshot(bootstrap.connector_session_id, grant.access_token))
                self.assertEqual(elevation_count, 1)
                self.assertEqual(
                    manager.snapshot(
                        bootstrap.connector_session_id,
                        grant.access_token,
                    ).stage,
                    DtsLiveOperationStage.COMPLETED,
                )
                manager.close_session(
                    bootstrap.connector_session_id,
                    grant.access_token,
                )
                system.close_dts_live_session(bootstrap.connector_session_id)
                self.assertFalse(runtime_path.exists())


def _prepare_library(
    library_root: Path, *, path_input: bool = False
) -> tuple[str, str]:
    package = inspect_strategy_package(
        ROOT / "strategy-packs" / "path-paired-placement"
        if path_input
        else STRATEGY_ROOT
    )
    catalog = load_catalog_yaml(
        (ASSET_ROOT / "catalog.yaml").read_text(encoding="utf-8")
    )
    profile_path = ASSET_ROOT / "profiles" / "universal-natural-grove.yaml"
    profile = load_profile_yaml(profile_path.read_text(encoding="utf-8"))
    if path_input:
        catalog = {
            "schema": "verdflow.target-asset-catalog",
            "version": "1",
            "catalog_id": "dts_default",
            "target_type": "dts",
            "provider_id": "dts.static-mesh",
            "assets": [
                {
                    "asset_id": "tree",
                    "display_name": "Tree",
                    "provider_metadata": {"model_name": "Fixture_Tree"},
                },
                {
                    "asset_id": "planter",
                    "display_name": "Planter",
                    "provider_metadata": {"model_name": "Fixture_Planter"},
                },
            ],
        }
        profile = build_profile_v2(
            profile_id="path_fixture",
            display_name="Paired path fixture",
            strategy_id=package.package_id,
            strategy_version=package.package_version,
            catalog=catalog,
            assignments={
                "vegetation": {
                    "candidates": [
                        {
                            "asset_id": "tree",
                            "weight": 1,
                            "scale": {"mode": "uniform_random", "min": 1, "max": 2},
                        }
                    ]
                },
                "planter": {
                    "candidates": [
                        {
                            "asset_id": "planter",
                            "weight": 1,
                            "scale": {"mode": "fixed", "value": 1},
                        }
                    ]
                },
            },
        )
    with LibraryStore.open(library_root) as store:
        store.install_verified_strategy(
            package.root,
            package_id=package.package_id,
            package_version=package.package_version,
            package_hash=package.package_hash,
            display_name=package.strategy["display_name"],
        )
        store.trust_strategy(package.package_hash, decided_by="test-user")
        store.save_asset_catalog(
            prepare_asset_catalog_document(catalog),
            expected_generation=0,
        )
        store.save_role_binding_profile(
            prepare_role_binding_profile_document(profile),
            expected_generation=0,
        )
    return package.package_hash, profile["profile_id"]


if __name__ == "__main__":
    unittest.main()
