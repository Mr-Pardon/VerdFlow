from __future__ import annotations

from pathlib import Path
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
from verdflow.dts_live_connector import DtsLiveConnectorApplication  # noqa: E402
from verdflow.library_store import LibraryStore  # noqa: E402
from verdflow.planting import (  # noqa: E402
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

    def test_multiple_regions_use_one_generation_sampling_and_export(self) -> None:
        self._run_regions(multi=True)

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

    def _run_regions(self, *, multi: bool, partial: bool = False, all_failed: bool = False, tweaked: bool = False, close_pending: bool = False, multi_catalog: bool = False) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package_hash, profile_id = _prepare_library(
                root / "library"
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
                    regions=[
                        {"region_id": "a", "name": "Area A", "coordinates": [[0, 0, 0], [100, 0, 0], [100, 100, 0], [0, 100, 0]]},
                        {"region_id": "b", "name": "Area B", "coordinates": [[1000, 1000, 0], [1100, 1000, 0], [1100, 1100, 0], [1000, 1100, 0]]},
                    ] if multi else None,
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
                            for batch in batches:
                                for line in lines[batch["start"]:batch["start"] + batch["count"]]:
                                    self.assertGreaterEqual(line["start"][0], batch["bbox"][0])
                                    self.assertLessEqual(line["start"][0], batch["bbox"][3])
                            if all_failed:
                                manager.accept_elevation(
                                    bootstrap.connector_session_id, grant.access_token,
                                    command.correlation, None, chunked=True,
                                    failed_regions=[{"region_id": batch["region_id"], "reason": "test terrain miss"} for batch in batches],
                                )
                                continue
                            if partial:
                                failed, good = batches
                                indices = list(range(good["start"], good["start"] + good["count"]))
                                expected_xml_count = good["count"]
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
                                    failed_regions=[{"region_id": failed["region_id"], "reason": "test terrain miss"}],
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
                            if partial:
                                self.assertEqual(xml_path.read_text("utf-8").count("<ModelPoint>"), expected_xml_count)
                                failures = manager.snapshot(bootstrap.connector_session_id, grant.access_token).sampling_failures
                                self.assertEqual([failure.region_id for failure in failures], ["a"])
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


def _prepare_library(library_root: Path) -> tuple[str, str]:
    package = inspect_strategy_package(STRATEGY_ROOT)
    catalog = load_catalog_yaml(
        (ASSET_ROOT / "catalog.yaml").read_text(encoding="utf-8")
    )
    profile_path = ASSET_ROOT / "profiles" / "universal-natural-grove.yaml"
    profile = load_profile_yaml(profile_path.read_text(encoding="utf-8"))
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
