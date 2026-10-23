from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
import shutil
import sys
import tempfile
from threading import Event, Thread
import unittest
import zipfile
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "worker-runtime" / "src"),
    str(ROOT / "packages" / "planting" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
    str(ROOT / "packages" / "core" / "src"),
    str(ROOT / "packages" / "library-store" / "src"),
    str(ROOT / "adapters" / "blender-worker" / "src"),
    str(ROOT / "adapters" / "dts" / "src"),
    str(ROOT / "adapters" / "strategy-worker" / "src"),
    str(ROOT / "apps" / "core-host" / "src"),
]

from verdflow.contracts import (  # noqa: E402
    CoordinateReferenceKind,
    JobCommandRef,
    JobStatus,
    RegionSummary,
    SourceKind,
    WorkerProcessStatus,
)
from verdflow.core import (  # noqa: E402
    AssetAssignmentRequest,
    OutputExportRequest,
    PlantingGenerationRequest,
    ServiceLoopState,
)
from verdflow.core_host import CoreHost, CoreHostConfig  # noqa: E402
from verdflow.core_host import (  # noqa: E402
    ElevationSamplingOutcome,
    GenerateAndAssignRequest,
    PlantRunRequest,
)
from verdflow.library_store import LibraryStore  # noqa: E402
from verdflow.planting import (  # noqa: E402
    load_catalog_yaml,
    load_profile_yaml,
    prepare_asset_catalog_document,
    prepare_role_binding_profile_document,
)
from verdflow.strategy_worker import inspect_strategy_package  # noqa: E402


TEST_TEMP_ROOT = ROOT / "tests" / ".tmp"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
STRATEGY_ROOT = ROOT / "strategy-packs" / "universal-street-tree"
LAYERED_STRATEGY_ROOT = (
    ROOT / "strategy-packs" / "layered-ecological-community-v2"
)
ASSET_ROOT = ROOT / "asset-packs" / "dts-default"


class ManualClock:
    def __init__(self) -> None:
        self.value = datetime(2030, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


class PlantingGenerationStageTests(unittest.TestCase):
    def test_assignment_control_cancel_converges_without_lease_expiry(self) -> None:
        for phase in ("before", "resolving", "after"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
                root = Path(temporary)
                with LibraryStore.open(root / "library") as library:
                    package = self._install_and_trust(library)
                    catalog, profile = self._install_asset_pack(library)
                    with CoreHost.create(
                        CoreHostConfig(root / "project", enable_blender_worker=False,
                                       enable_project_maintenance=False),
                        project_name="Assignment cancellation", strategy_library=library,
                    ) as host:
                        scene_id, region_set_id = self._scene_and_regions(host)
                        generated = host.generate_points(PlantingGenerationRequest(
                            scene_revision_id=scene_id, region_set_id=region_set_id,
                            selected_region_ids=("street_region",),
                            strategy_package_hash=package.package_hash, random_seed=17,
                        ))
                        job = host.application.submit_asset_assignment(AssetAssignmentRequest(
                            point_set_id=generated.point_set.point_set_id, target_type="dts",
                            catalog_id=catalog.catalog_id, profile_id=profile.profile_id, random_seed=17,
                        ))
                        def signal():
                            thread = Thread(target=host.registry.request_cancel, args=(job,))
                            thread.start()
                            thread.join(1.0)
                            self.assertFalse(thread.is_alive())
                        from verdflow.core.assignment_worker import resolve_persisted_asset_assignments
                        resolved = Event()
                        def resolve(*args, **kwargs):
                            if phase == "resolving":
                                signal()
                            result = resolve_persisted_asset_assignments(*args, **kwargs)
                            resolved.set()
                            if phase == "after":
                                signal()
                            return result
                        if phase == "before":
                            signal()
                        with patch("verdflow.core.assignment_worker.resolve_persisted_asset_assignments",
                                   side_effect=resolve) as resolver:
                            host.service_loop.run_once()
                        cancelled = host.runtime.get(job.job_id)
                        self.assertEqual(cancelled.status, JobStatus.CANCELLED)
                        self.assertIsNone(cancelled.result_artifact_id)
                        self.assertIsNone(host.runtime.events(job.job_id)[-1].message)
                        self.assertEqual(host.service_loop.state, ServiceLoopState.RUNNING)
                        self.assertEqual(resolved.is_set(), phase == "after")
                        self.assertEqual(resolver.call_count, int(phase != "before"))
                        self.assertEqual(host.store.get_point_set(generated.point_set.point_set_id),
                                         generated.point_set)

    def test_current_catalog_is_frozen_for_one_run_without_a_profile(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            root = Path(temporary)
            with LibraryStore.open(root / "library") as library:
                package = self._install_and_trust(library)
                catalog_info, _ = self._install_asset_pack(library)
                catalog = load_catalog_yaml(
                    (ASSET_ROOT / "catalog.yaml").read_text(encoding="utf-8")
                )
                profile = load_profile_yaml(
                    (
                        ASSET_ROOT
                        / "profiles"
                        / "universal-street-tree.yaml"
                    ).read_text(encoding="utf-8")
                )
                config = CoreHostConfig(
                    root / "current-catalog.verdflow",
                    enable_blender_worker=False,
                    enable_project_maintenance=False,
                    strategy_timeout_seconds=30.0,
                )
                with CoreHost.create(
                    config,
                    project_name="Current Catalog assignment",
                    strategy_library=library,
                ) as host:
                    scene_id, region_set_id = self._scene_and_regions(host)
                    generated = host.generate_points(
                        PlantingGenerationRequest(
                            scene_revision_id=scene_id,
                            region_set_id=region_set_id,
                            selected_region_ids=("street_region",),
                            strategy_package_hash=package.package_hash,
                            random_seed=2026071301,
                        )
                    )
                    assigned = host.assign_assets(
                        AssetAssignmentRequest(
                            point_set_id=generated.point_set.point_set_id,
                            target_type="dts",
                            catalog_id=catalog["catalog_id"],
                            random_seed=17,
                            overrides=profile["assignments"],
                        )
                    )
                    self.assertEqual(assigned.job.max_attempts, 2)
                    assignment_stages = {
                        event.stage
                        for event in host.runtime.events(assigned.job.job_id)
                    }
                    self.assertIn(
                        "resolving_asset_assignments",
                        assignment_stages,
                    )
                    self.assertIn(
                        "validating_asset_assignments",
                        assignment_stages,
                    )
                    binding = host.store.get_effective_binding_snapshot(
                        assigned.assignments.effective_binding_snapshot_id
                    )
                    asset_use = host.store.get_target_asset_use_snapshot(
                        binding.target_asset_use_snapshot_id
                    )
                    with host.store.open_artifact(
                        asset_use.snapshot_artifact_id
                    ) as stream:
                        snapshot = json.load(stream)
                    self.assertEqual(snapshot["source"]["kind"], "catalog")
                    self.assertEqual(
                        snapshot["source"]["generation"],
                        catalog_info.generation,
                    )

                    changed = dict(catalog)
                    changed["display_name"] = "Changed after Plant Run"
                    library.save_asset_catalog(
                        prepare_asset_catalog_document(changed),
                        expected_generation=catalog_info.generation,
                    )
                    with host.store.open_artifact(
                        asset_use.snapshot_artifact_id
                    ) as stream:
                        frozen = json.load(stream)
                    self.assertEqual(frozen, snapshot)

    def test_expired_assignment_lease_retries_same_points_without_failing_loop(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            root = Path(temporary)
            with LibraryStore.open(root / "library") as library:
                package = self._install_and_trust(library)
                catalog, profile = self._install_asset_pack(library)
                config = CoreHostConfig(
                    root / "assignment-retry.verdflow",
                    enable_blender_worker=False,
                    enable_project_maintenance=False,
                    lease_seconds=10.0,
                    strategy_timeout_seconds=30.0,
                )
                with CoreHost.create(
                    config,
                    project_name="Assignment retry",
                    strategy_library=library,
                ) as host:
                    scene_id, region_set_id = self._scene_and_regions(host)
                    generated = host.generate_points(
                        PlantingGenerationRequest(
                            scene_revision_id=scene_id,
                            region_set_id=region_set_id,
                            selected_region_ids=("street_region",),
                            strategy_package_hash=package.package_hash,
                            random_seed=2026071301,
                        )
                    )
                    clock = ManualClock()
                    host.runtime.clock = clock
                    host.runtime.lease_duration = timedelta(seconds=10.0)
                    submitted = host.application.submit_asset_assignment(
                        AssetAssignmentRequest(
                            point_set_id=generated.point_set.point_set_id,
                            target_type="dts",
                            catalog_id=catalog.catalog_id,
                            random_seed=17,
                            profile_id=profile.profile_id,
                        )
                    )

                    from verdflow.core.assignment_worker import (
                        resolve_persisted_asset_assignments as resolve_fast,
                    )

                    calls = 0

                    def expire_first_lease(*args, **kwargs):
                        nonlocal calls
                        calls += 1
                        if calls == 1:
                            clock.advance(timedelta(seconds=11.0))
                        return resolve_fast(*args, **kwargs)

                    with patch(
                        "verdflow.core.assignment_worker."
                        "resolve_persisted_asset_assignments",
                        side_effect=expire_first_lease,
                    ):
                        completed = host.service_loop.run_until_terminal(
                            submitted.job_id,
                            timeout_seconds=5.0,
                        )

                    self.assertEqual(completed.status, JobStatus.SUCCEEDED)
                    self.assertEqual(completed.max_attempts, 2)
                    self.assertEqual(completed.attempt_count, 2)
                    self.assertEqual(
                        host.service_loop.state,
                        ServiceLoopState.RUNNING,
                    )
                    with host.store.open_artifact(
                        completed.command.artifact_id
                    ) as stream:
                        command = json.load(stream)
                    self.assertEqual(
                        command["point_set_id"],
                        generated.point_set.point_set_id,
                    )

    def test_core_host_runs_complete_planting_flow_idempotently(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            root = Path(temporary)
            with LibraryStore.open(root / "library") as library:
                package = self._install_and_trust(library)
                catalog, profile = self._install_asset_pack(library)
                config = CoreHostConfig(
                    root / "plant-run.verdflow",
                    enable_blender_worker=False,
                    enable_project_maintenance=False,
                    lease_seconds=10.0,
                    strategy_timeout_seconds=30.0,
                    strategy_heartbeat_interval_seconds=0.05,
                )
                with CoreHost.create(
                    config,
                    project_name="Complete planting run",
                    strategy_library=library,
                ) as host:
                    scene_id, region_set_id = self._scene_and_regions(host)
                    resolved_by_parent = {}

                    def fixture_elevation(request, *, timeout_seconds):
                        self.assertGreater(timeout_seconds, 0)
                        point_set = resolved_by_parent.get(
                            request.parent_point_set_id
                        )
                        if point_set is None:
                            point_set = self._resolve_fixture_elevation(
                                host,
                                request.parent_point_set_id,
                            )
                            resolved_by_parent[request.parent_point_set_id] = (
                                point_set
                            )
                        return ElevationSamplingOutcome(
                            host.store.get_job(point_set.producer_job_id),
                            point_set,
                        )

                    host.sample_elevation = fixture_elevation  # type: ignore[method-assign]
                    request = PlantRunRequest(
                        scene_revision_id=scene_id,
                        region_set_id=region_set_id,
                        selected_region_ids=("street_region",),
                        strategy_package_hash=package.package_hash,
                        random_seed=2026071301,
                        elevation_provider_id="verdflow.fixture.elevation",
                        target_type=catalog.target_type,
                        catalog_id=catalog.catalog_id,
                        profile_id=profile.profile_id,
                        export_provider_id="verdflow.dts.xml",
                        offset=(0.0, 0.0, 0.0),
                    )

                    prepared = host.generate_and_assign(
                        GenerateAndAssignRequest(
                            scene_revision_id=scene_id,
                            region_set_id=region_set_id,
                            selected_region_ids=("street_region",),
                            strategy_package_hash=package.package_hash,
                            random_seed=2026071301,
                            target_type=catalog.target_type,
                            catalog_id=catalog.catalog_id,
                            profile_id=profile.profile_id,
                        )
                    )
                    self.assertFalse(resolved_by_parent)
                    first = host.run_planting(request)
                    repeated = host.run_planting(request)

                    self.assertEqual(
                        first.planting_run_id,
                        first.generation.point_set.planting_run_id,
                    )
                    self.assertEqual(
                        first.elevation.point_set.parent_point_set_id,
                        first.generation.point_set.point_set_id,
                    )
                    self.assertEqual(
                        first.assignment.assignments.point_set_id,
                        first.generation.point_set.point_set_id,
                    )
                    self.assertEqual(prepared.generation, first.generation)
                    self.assertEqual(prepared.assignment, first.assignment)
                    self.assertEqual(
                        first.output.export.asset_assignment_set_id,
                        first.assignment.assignments.asset_assignment_set_id,
                    )
                    self.assertEqual(
                        first.output.export.record_count,
                        first.elevation.point_set.point_count,
                    )
                    self.assertEqual(first, repeated)

    def test_core_host_assigns_exact_target_assets_after_generation(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            root = Path(temporary)
            with LibraryStore.open(root / "library") as library:
                package = self._install_and_trust(library)
                catalog, profile = self._install_asset_pack(library)
                config = CoreHostConfig(
                    root / "assignment.verdflow",
                    enable_blender_worker=False,
                    enable_project_maintenance=False,
                    lease_seconds=10.0,
                    strategy_timeout_seconds=30.0,
                    strategy_heartbeat_interval_seconds=0.05,
                )
                with CoreHost.create(
                    config,
                    project_name="Asset assignment",
                    strategy_library=library,
                ) as host:
                    scene_id, region_set_id = self._scene_and_regions(host)
                    generated = host.generate_points(
                        PlantingGenerationRequest(
                            scene_revision_id=scene_id,
                            region_set_id=region_set_id,
                            selected_region_ids=("street_region",),
                            strategy_package_hash=package.package_hash,
                            random_seed=2026071301,
                        )
                    )
                    elevated = self._resolve_fixture_elevation(
                        host,
                        generated.point_set.point_set_id,
                    )
                    disabled = host.generate_and_assign(GenerateAndAssignRequest(
                        scene_revision_id=scene_id,
                        region_set_id=region_set_id,
                        selected_region_ids=("street_region",),
                        strategy_package_hash=package.package_hash,
                        random_seed=2026071301,
                        target_type="dts",
                        catalog_id=None,
                        profile_id=profile.profile_id,
                        binding_overrides={"tree_accent": {"enabled": False}},
                    ))
                    with host.store.open_artifact(generated.point_set.point_artifact_id) as stream:
                        full = json.load(stream)
                    with host.store.open_artifact(disabled.generation.point_set.point_artifact_id) as stream:
                        partial = json.load(stream)
                    self.assertEqual(partial["points"], [point for point in full["points"] if point["role_key"] != "tree_accent"])
                    self.assertEqual(disabled.assignment.assignments.point_count, len(partial["points"]))
                    self.assertNotIn("SM_Oak_1D", {item.asset_id for item in host.store.list_asset_assignment_summaries(
                        disabled.assignment.assignments.asset_assignment_set_id,
                    )})
                    unrelated = host.generate_points(
                        PlantingGenerationRequest(
                            scene_revision_id=scene_id,
                            region_set_id=region_set_id,
                            selected_region_ids=("street_region",),
                            strategy_package_hash=package.package_hash,
                            random_seed=2026071302,
                        )
                    )
                    unrelated_elevated = self._resolve_fixture_elevation(
                        host,
                        unrelated.point_set.point_set_id,
                    )
                    shutil.rmtree(
                        library.strategy_package_root(package.package_hash)
                    )
                    request = AssetAssignmentRequest(
                        point_set_id=elevated.point_set_id,
                        target_type=catalog.target_type,
                        catalog_id=catalog.catalog_id,
                        profile_id=profile.profile_id,
                        random_seed=83,
                    )

                    first = host.assign_assets(request)
                    repeated = host.assign_assets(request)
                    finalized_again = (
                        host.application.finalize_asset_assignment(
                            first.job.job_id
                        )
                    )

                    self.assertEqual(first.job.job_id, repeated.job.job_id)
                    self.assertEqual(first.assignments, finalized_again)
                    self.assertEqual(first.assignments.target_type, "dts")
                    self.assertEqual(
                        first.assignments.point_count,
                        generated.point_set.point_count,
                    )
                    self.assertGreater(
                        len(
                            host.store.list_asset_assignment_summaries(
                                first.assignments.asset_assignment_set_id
                            )
                        ),
                        1,
                    )
                    with host.store.open_artifact(
                        first.job.command.artifact_id
                    ) as stream:
                        command = json.load(stream)
                    self.assertEqual(command["target_type"], "dts")
                    self.assertNotIn("path", json.dumps(command).lower())
                    export_request = OutputExportRequest(
                        point_set_id=elevated.point_set_id,
                        asset_assignment_set_id=(
                            first.assignments.asset_assignment_set_id
                        ),
                        provider_id="verdflow.dts.xml",
                        offset=(100.0, 200.0, 3.0),
                    )
                    exported = host.export_output(export_request)
                    repeated_export = host.export_output(export_request)
                    self.assertEqual(
                        exported.job.job_id,
                        repeated_export.job.job_id,
                    )
                    self.assertEqual(exported.export, repeated_export.export)
                    self.assertEqual(
                        exported.export.record_count,
                        elevated.point_count,
                    )
                    latest_export = host.export_output(
                        replace(export_request, offset=(0.0, 0.0, 0.0))
                    )
                    self.assertNotEqual(
                        exported.export.export_id,
                        latest_export.export.export_id,
                    )
                    self.assertEqual(
                        host.store.get_latest_export_record(
                            elevated.point_set_id,
                            first.assignments.asset_assignment_set_id,
                        ),
                        latest_export.export,
                    )
                    with host.store.open_artifact(
                        exported.export.output_artifact_id
                    ) as stream:
                        xml = stream.read().decode("utf-8")
                    self.assertEqual(
                        xml.count("<ModelPoint>"),
                        elevated.point_count,
                    )
                    self.assertIn("<ModelName>", xml)

                    summaries = host.store.list_asset_assignment_summaries(
                        first.assignments.asset_assignment_set_id,
                    )
                    selected_asset = summaries[0]
                    subset_request = replace(export_request, selected_asset_ids=(selected_asset.asset_id,))
                    subset = host.export_output(subset_request)
                    self.assertEqual(subset.export.record_count, selected_asset.point_count)
                    with host.store.open_artifact(subset.export.output_artifact_id) as stream:
                        subset_xml = stream.read().decode("utf-8")
                    self.assertEqual(subset_xml.count("<ModelPoint>"), selected_asset.point_count)
                    with host.store.open_artifact(subset.job.command.artifact_id) as stream:
                        subset_command = json.load(stream)
                    self.assertEqual(subset_command["selected_asset_ids"], [selected_asset.asset_id])
                    zero_offset = host.export_output(replace(subset_request, offset=(0.0, 0.0, 0.0)))
                    self.assertEqual(zero_offset.export.point_set_id, subset.export.point_set_id)
                    self.assertEqual(zero_offset.export.record_count, selected_asset.point_count)
                    self.assertEqual(host.export_output(export_request).export.record_count, elevated.point_count)
                    with self.assertRaises(ValueError):
                        host.export_output(replace(export_request, selected_asset_ids=()))
                    with self.assertRaises(ValueError):
                        host.export_output(replace(export_request, selected_asset_ids=("absent",)))

                    with self.assertRaisesRegex(ValueError, "direct generated parent"):
                        host.export_output(
                            OutputExportRequest(
                                point_set_id=unrelated_elevated.point_set_id,
                                asset_assignment_set_id=(
                                    first.assignments.asset_assignment_set_id
                                ),
                                provider_id="verdflow.dts.xml",
                                offset=(0.0, 0.0, 0.0),
                            )
                        )

    def test_core_host_generates_and_snapshots_trusted_strategy(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            root = Path(temporary)
            with LibraryStore.open(root / "library") as library:
                package = self._install_and_trust(library)
                config = CoreHostConfig(
                    root / "generation.verdflow",
                    enable_blender_worker=False,
                    enable_project_maintenance=False,
                    lease_seconds=10.0,
                    strategy_timeout_seconds=30.0,
                    strategy_heartbeat_interval_seconds=0.05,
                )
                with CoreHost.create(
                    config,
                    project_name="Planting generation",
                    strategy_library=library,
                ) as host:
                    scene_id, region_set_id = self._scene_and_regions(host)
                    request = PlantingGenerationRequest(
                        scene_revision_id=scene_id,
                        region_set_id=region_set_id,
                        selected_region_ids=("street_region",),
                        strategy_package_hash=package.package_hash,
                        random_seed=2026071301,
                    )

                    first = host.generate_points(request)
                    second = host.generate_points(request)
                    with host.store.open_artifact(first.point_set.point_artifact_id) as stream:
                        full_points = json.load(stream)
                    self.assertIn("tree_accent", {point["role_key"] for point in full_points["points"]})
                    filtered = host.generate_points(replace(request, excluded_role_keys=("tree_accent",)))
                    with host.store.open_artifact(filtered.point_set.point_artifact_id) as stream:
                        filtered_points = json.load(stream)
                    self.assertEqual(filtered_points["points"], [
                        point for point in full_points["points"] if point["role_key"] != "tree_accent"
                    ])
                    self.assertLess(filtered.point_set.point_count, first.point_set.point_count)
                    assert filtered.job.result_artifact_id is not None
                    with host.store.open_artifact(filtered.job.result_artifact_id) as stream:
                        worker_result = json.load(stream)
                    raw_artifact_id = next(
                        item["artifact_id"] for item in worker_result["output_artifacts"]
                        if item["role"] == "points"
                    )
                    with host.store.open_artifact(raw_artifact_id) as stream:
                        raw_points = json.load(stream)
                    self.assertIn("tree_accent", {point["role_key"] for point in raw_points["points"]})
                    self.assertNotIn("tree_accent", {point["role_key"] for point in filtered_points["points"]})
                    self.assertIsNone(filtered.point_set.parent_point_set_id)
                    run = host.store.get_planting_run(
                        first.point_set.planting_run_id
                    )
                    snapshot = host.store.get_strategy_package_snapshot_by_run(
                        run.planting_run_id
                    )
                    process = host.store.get_worker_process(
                        first.job.job_id,
                        1,
                    )
                    assert snapshot is not None

                    self.assertEqual(first.job.job_id, second.job.job_id)
                    self.assertEqual(
                        first.point_set.point_set_id,
                        second.point_set.point_set_id,
                    )
                    self.assertGreater(first.point_set.point_count, 0)
                    self.assertEqual(run.strategy_id, package.package_id)
                    self.assertEqual(run.strategy_package_hash, package.package_hash)
                    self.assertEqual(snapshot.package_hash, package.package_hash)
                    self.assertEqual(
                        process.adapter_id,
                        "verdflow-strategy-worker",
                    )
                    self.assertEqual(
                        process.status,
                        WorkerProcessStatus.EXITED,
                    )
                    self.assertIsNotNone(process.finished_at)
                    with host.store.open_artifact(
                        first.job.command.artifact_id
                    ) as stream:
                        command = json.load(stream)
                    self.assertNotIn("path", json.dumps(command).lower())
                    self.assertEqual(
                        command["selected_region_ids"],
                        ["street_region"],
                    )
                    archive_path = root / "snapshot.zip"
                    host.store.materialize_artifact(
                        snapshot.package_artifact_id,
                        archive_path,
                    )
                    with zipfile.ZipFile(archive_path) as archive:
                        self.assertIn(
                            "strategy-package.json",
                            archive.namelist(),
                        )

    def test_core_forwards_role_exclusions_to_role_aware_strategy(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            root = Path(temporary)
            with LibraryStore.open(root / "library") as library:
                package = self._install_and_trust(
                    library,
                    LAYERED_STRATEGY_ROOT,
                )
                config = CoreHostConfig(
                    root / "selective-role.verdflow",
                    enable_blender_worker=False,
                    enable_project_maintenance=False,
                    lease_seconds=10.0,
                    strategy_timeout_seconds=30.0,
                )
                with CoreHost.create(
                    config,
                    project_name="Selective role generation",
                    strategy_library=library,
                ) as host:
                    scene_id, region_set_id = self._scene_and_regions(host)
                    request = PlantingGenerationRequest(
                        scene_revision_id=scene_id,
                        region_set_id=region_set_id,
                        selected_region_ids=("street_region",),
                        strategy_package_hash=package.package_hash,
                        random_seed=43,
                        excluded_role_keys=("groundcover_matrix",),
                        args={"strategy_parameters": {"groundcover_coverage_percent": 5}},
                    )
                    generated = host.generate_points(request)
                    repeated = host.generate_points(request)
                    full = host.generate_points(replace(request, excluded_role_keys=()))
                    self.assertEqual(generated.job.job_id, repeated.job.job_id)
                    self.assertNotEqual(generated.job.job_id, full.job.job_id)
                    with host.store.open_artifact(generated.job.command.artifact_id) as stream:
                        command = json.load(stream)
                    self.assertEqual(command["excluded_role_keys"], ["groundcover_matrix"])
                    self.assertEqual(command["args"]["strategy_parameters"]["groundcover_coverage_percent"], 5)

                    assert generated.job.result_artifact_id is not None
                    with host.store.open_artifact(
                        generated.job.result_artifact_id
                    ) as stream:
                        worker_result = json.load(stream)
                    points_artifact_id = next(
                        item["artifact_id"]
                        for item in worker_result["output_artifacts"]
                        if item["role"] == "points"
                    )
                    with host.store.open_artifact(points_artifact_id) as stream:
                        worker_points = json.load(stream)

                    self.assertGreater(worker_points["point_count"], 0)
                    self.assertEqual(
                        {point["role_key"] for point in worker_points["points"]},
                        {"focal_structure"},
                    )
                    with host.store.open_artifact(generated.point_set.point_artifact_id) as stream:
                        final_points = json.load(stream)
                    self.assertEqual(worker_points["points"], final_points["points"])

    def test_execution_rechecks_trust_after_submission(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            root = Path(temporary)
            with LibraryStore.open(root / "library") as library:
                package = self._install_and_trust(library)
                config = CoreHostConfig(
                    root / "revoked.verdflow",
                    enable_blender_worker=False,
                    enable_project_maintenance=False,
                )
                with CoreHost.create(
                    config,
                    project_name="Revoked strategy",
                    strategy_library=library,
                ) as host:
                    scene_id, region_set_id = self._scene_and_regions(host)
                    request = PlantingGenerationRequest(
                        scene_revision_id=scene_id,
                        region_set_id=region_set_id,
                        selected_region_ids=("street_region",),
                        strategy_package_hash=package.package_hash,
                        random_seed=7,
                    )
                    submitted = host.application.submit_planting_generation(
                        request
                    )
                    library.revoke_strategy(
                        package.package_hash,
                        decided_by="test",
                    )
                    host.start()
                    completed = host.service_loop.run_until_terminal(
                        submitted.job_id,
                        timeout_seconds=10.0,
                    )

                    self.assertEqual(completed.status, JobStatus.FAILED)
                    assert completed.error is not None
                    self.assertEqual(
                        completed.error.code,
                        "strategy_trust_revoked",
                    )
                    self.assertIsNone(
                        host.store.get_point_set_by_job(submitted.job_id)
                    )

    @staticmethod
    def _install_and_trust(
        library: LibraryStore,
        strategy_root: Path = STRATEGY_ROOT,
    ):
        package = inspect_strategy_package(strategy_root)
        library.install_verified_strategy(
            package.root,
            package_id=package.package_id,
            package_version=package.package_version,
            package_hash=package.package_hash,
            display_name=package.strategy["display_name"],
        )
        library.trust_strategy(
            package.package_hash,
            decided_by="test",
        )
        return package

    @staticmethod
    def _install_asset_pack(library: LibraryStore):
        catalog = load_catalog_yaml(
            (ASSET_ROOT / "catalog.yaml").read_text(encoding="utf-8")
        )
        profile = load_profile_yaml(
            (
                ASSET_ROOT
                / "profiles"
                / "universal-street-tree.yaml"
            ).read_text(encoding="utf-8")
        )
        catalog_info = library.save_asset_catalog(
            prepare_asset_catalog_document(catalog),
            expected_generation=0,
        )
        profile_info = library.save_role_binding_profile(
            prepare_role_binding_profile_document(profile),
            expected_generation=0,
        )
        return catalog_info, profile_info

    @staticmethod
    def _resolve_fixture_elevation(host: CoreHost, point_set_id: str):
        store = host.store
        parent = store.get_point_set(point_set_id)
        with store.open_artifact(parent.point_artifact_id) as stream:
            document = json.load(stream)
        document["kind"] = "elevation_resolved"
        document["resolution_policy"] = "sample_hit_else_generated"
        for point in document["points"]:
            position = list(point["generated_position"])
            point["terrain_sample"] = {
                "hit": True,
                "position": position,
                "normal": [0.0, 0.0, 1.0],
                "target": "fixture-terrain",
            }
            point["resolved_position"] = position
        point_artifact = store.put_bytes(
            json.dumps(
                document,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
            media_type="application/vnd.verdflow.points+json",
            schema_id="verdflow.points",
            schema_version="2",
        )
        command_artifact = store.put_bytes(
            b'{"operation":"fixture-elevation"}',
            media_type="application/vnd.verdflow.command+json",
        )
        job = host.runtime.submit(
            JobCommandRef(
                command_type="verdflow.fixture.elevation",
                command_version="1",
                artifact_id=command_artifact.artifact_id,
            ),
            idempotency_key=f"fixture-elevation:{point_set_id}",
        )
        claimed = host.runtime.claim_next(
            "fixture-elevation-worker",
            command_types=("verdflow.fixture.elevation",),
        )
        assert claimed is not None
        result_artifact = store.put_bytes(
            b'{"result_kind":"fixture_elevation"}',
            media_type="application/json",
        )
        host.runtime.succeed(
            claimed.lease,
            result_artifact_id=result_artifact.artifact_id,
        )
        bounds = (
            parent.min_x,
            parent.min_y,
            parent.min_z,
            parent.max_x,
            parent.max_y,
            parent.max_z,
        )
        assert all(value is not None for value in bounds)
        return store.create_elevation_point_set(
            parent_point_set_id=parent.point_set_id,
            producer_job_id=job.job_id,
            producer_id="fixture-elevation",
            producer_version="1",
            input_hash=f"sha256:fixture-elevation:{point_set_id}",
            coordinate_frame_id=parent.coordinate_frame_id,
            point_artifact_id=point_artifact.artifact_id,
            resolution_policy="sample_hit_else_generated",
            point_count=parent.point_count,
            hit_count=parent.point_count,
            miss_count=0,
            bounds=tuple(float(value) for value in bounds),
            roles=store.list_point_set_roles(parent.point_set_id),
        )

    @staticmethod
    def _scene_and_regions(host: CoreHost) -> tuple[str, str]:
        store = host.store
        source_artifact = store.put_bytes(
            b"fixture fbx",
            media_type="model/vnd.autodesk.fbx",
        )
        source = store.register_source(
            kind=SourceKind.FBX,
            original_name="fixture.fbx",
            artifact_id=source_artifact.artifact_id,
        )
        frame = store.create_coordinate_frame(
            name="Source scene world",
            handedness="right-handed",
            up_axis="+Z",
            linear_unit="meter",
            crs_kind=CoordinateReferenceKind.UNKNOWN,
        )
        manifest = store.put_bytes(
            b'{"schema":"verdflow.scene-manifest","version":"1"}',
            media_type="application/json",
        )
        scene = store.create_scene_revision(
            source_id=source.source_id,
            importer_id="fixture",
            importer_version="1",
            coordinate_frame_id=frame.coordinate_frame_id,
            scene_manifest_artifact_id=manifest.artifact_id,
        )
        command_artifact = store.put_bytes(
            b'{"operation":"fixture-region"}',
            media_type="application/vnd.verdflow.command+json",
        )
        region_job = host.runtime.submit(
            JobCommandRef(
                command_type="verdflow.fixture.region",
                command_version="1",
                artifact_id=command_artifact.artifact_id,
            ),
            idempotency_key="fixture-region",
        )
        claimed = host.runtime.claim_next(
            "fixture-worker",
            command_types=("verdflow.fixture.region",),
        )
        assert claimed is not None
        result_artifact = store.put_bytes(
            b'{"result_kind":"region_fixture"}',
            media_type="application/json",
        )
        host.runtime.succeed(
            claimed.lease,
            result_artifact_id=result_artifact.artifact_id,
        )
        geometry_document = {
            "schema": "verdflow.regions",
            "version": "1",
            "coordinate_space": "source_scene_world",
            "offset_applied": False,
            "region_count": 1,
            "regions": [
                {
                    "id": "street_region",
                    "bbox": [0.0, -1.25, 2.0, 40.0, 1.25, 2.0],
                    "rings": [
                        {
                            "index": 0,
                            "role": "outer",
                            "loop": [
                                [0.0, -1.25, 2.0],
                                [40.0, -1.25, 2.0],
                                [40.0, 1.25, 2.0],
                                [0.0, 1.25, 2.0],
                            ],
                        }
                    ],
                }
            ],
        }
        geometry = store.put_bytes(
            json.dumps(geometry_document).encode("utf-8"),
            media_type="application/vnd.verdflow.regions+json",
            schema_id="verdflow.regions",
            schema_version="1",
        )
        metrics = store.put_bytes(
            b'{"schema":"verdflow.region-metrics","version":"1","regions":[]}',
            media_type="application/json",
        )
        region_set = store.create_region_set(
            scene_revision_id=scene.scene_revision_id,
            producer_job_id=region_job.job_id,
            extractor_id="fixture",
            extractor_version="1",
            input_hash="sha256:fixture-regions",
            coordinate_frame_id=frame.coordinate_frame_id,
            geometry_artifact_id=geometry.artifact_id,
            metrics_artifact_id=metrics.artifact_id,
            regions=(
                RegionSummary(
                    region_id="street_region",
                    ordinal=0,
                    label="Street greenbelt",
                    tags="street grass",
                    classification="street_tree_greenbelt",
                    strategy_hint="universal_street_tree",
                    footprint_area_xy=100.0,
                    boundary_perimeter_xy=85.0,
                    min_x=0.0,
                    min_y=-1.25,
                    min_z=2.0,
                    max_x=40.0,
                    max_y=1.25,
                    max_z=2.0,
                    metrics_json="{}",
                ),
            ),
        )
        return scene.scene_revision_id, region_set.region_set_id
