from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "worker-runtime" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
    str(ROOT / "packages" / "planting" / "src"),
    str(ROOT / "packages" / "core" / "src"),
    str(ROOT / "adapters" / "blender-worker" / "src"),
]

from verdflow.blender_worker import (  # noqa: E402
    BLENDER_EXTRACT_REGIONS_COMMAND,
    BLENDER_INSPECT_ASSET_LIBRARY_COMMAND,
    BLENDER_INSPECT_SCENE_COMMAND,
    BLENDER_PROBE_COMMAND,
    BLENDER_SAMPLE_ELEVATION_COMMAND,
    BLENDER_WORKER_ADAPTER_ID,
    BlenderAssetLibraryRegistry,
    BlenderWorkerConfig,
    BlenderWorkerSupervisor,
)
from verdflow.contracts import (  # noqa: E402
    CoordinateReferenceKind,
    JobCommandRef,
    JobStatus,
    PointRoleSummary,
    RegionSummary,
    SourceAssemblyMemberSpec,
    SourceKind,
    WorkerProcessInfo,
    WorkerProcessStatus,
)
from verdflow.core import (  # noqa: E402
    ElevationProviderDefinition,
    ElevationSamplingRequest,
    ElevationSamplingService,
    JobRuntime,
    PointSetImportService,
    RegionExtractionProviderDefinition,
    RegionExtractionRequest,
    RegionExtractionService,
)
from verdflow.project_store import (  # noqa: E402
    ProcessStatus,
    ProjectStore,
    SystemProcessInspector,
)


TEST_TEMP_ROOT = ROOT / "tests" / ".tmp"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
FAKE_WORKER = (
    ROOT / "tests" / "integration" / "helpers" / "fake_blender_worker.py"
)


def _succeeded_fixture_job(
    store: ProjectStore,
    runtime: JobRuntime,
    command_type: str,
    key: str,
):
    command = store.put_bytes(
        json.dumps({"operation": key}, sort_keys=True).encode("utf-8"),
        media_type="application/vnd.verdflow.command+json",
    )
    job = runtime.submit(
        JobCommandRef(command_type, "1", command.artifact_id),
        idempotency_key=key,
    )
    claimed = runtime.claim_next(
        "fixture-worker",
        command_types=(command_type,),
    )
    assert claimed is not None
    result = store.put_bytes(
        json.dumps({"result_kind": key}, sort_keys=True).encode("utf-8"),
        media_type="application/json",
    )
    runtime.succeed(claimed.lease, result_artifact_id=result.artifact_id)
    return job


def _generated_point_fixture(store: ProjectStore, runtime: JobRuntime):
    source_artifact = store.put_bytes(
        b"fake fbx bytes",
        media_type="model/vnd.autodesk.fbx",
    )
    source = store.register_source(
        kind=SourceKind.FBX,
        original_name="scene.fbx",
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
        importer_id="fixture-importer",
        importer_version="1",
        coordinate_frame_id=frame.coordinate_frame_id,
        scene_manifest_artifact_id=manifest.artifact_id,
    )
    region_job = _succeeded_fixture_job(
        store,
        runtime,
        "verdflow.fixture.regions",
        "fixture-regions",
    )
    geometry = store.put_bytes(
        b'{"schema":"verdflow.regions","version":"1","regions":[]}',
        media_type="application/vnd.verdflow.regions+json",
    )
    metrics = store.put_bytes(b'{"regions":[]}', media_type="application/json")
    region_set = store.create_region_set(
        scene_revision_id=scene.scene_revision_id,
        producer_job_id=region_job.job_id,
        extractor_id="fixture-extractor",
        extractor_version="1",
        input_hash="sha256:" + "1" * 64,
        coordinate_frame_id=frame.coordinate_frame_id,
        geometry_artifact_id=geometry.artifact_id,
        metrics_artifact_id=metrics.artifact_id,
        regions=(
            RegionSummary(
                region_id="region_1",
                ordinal=0,
                label="Fixture region",
                tags="",
                classification="ordinary",
                strategy_hint=None,
                footprint_area_xy=100.0,
                boundary_perimeter_xy=40.0,
                min_x=0.0,
                min_y=0.0,
                min_z=0.0,
                max_x=10.0,
                max_y=10.0,
                max_z=5.0,
                metrics_json="{}",
            ),
        ),
    )
    generation_job = _succeeded_fixture_job(
        store,
        runtime,
        "verdflow.fixture.generate",
        "fixture-generate",
    )
    points = {
        "schema": "verdflow.points",
        "version": "2",
        "strategy": {
            "strategy_id": "fixture-strategy",
            "strategy_version": "1",
        },
        "coordinate_space": "source_scene_world",
        "offset_applied": False,
        "kind": "generated",
        "resolution_policy": "generated_position",
        "point_count": 1,
        "points": [
            {
                "id": "point_1",
                "region_id": "region_1",
                "role_key": "canopy_tree",
                "generated_position": [1.0, 2.0, 3.0],
                "terrain_sample": None,
                "resolved_position": [1.0, 2.0, 3.0],
                "rotation_quaternion_xyzw": [0.0, 0.0, 0.0, 1.0],
            }
        ],
    }
    point_artifact = store.put_bytes(
        json.dumps(points, sort_keys=True).encode("utf-8"),
        media_type="application/vnd.verdflow.points+json",
        schema_id="verdflow.points",
        schema_version="2",
    )
    _, point_set = store.create_generated_point_set(
        scene_revision_id=scene.scene_revision_id,
        region_set_id=region_set.region_set_id,
        producer_job_id=generation_job.job_id,
        strategy_id="fixture-strategy",
        strategy_version="1",
        strategy_package_hash="sha256:" + "2" * 64,
        generator_id="fixture-generator",
        generator_version="1",
        random_seed=7,
        input_hash="sha256:" + "3" * 64,
        coordinate_frame_id=frame.coordinate_frame_id,
        point_artifact_id=point_artifact.artifact_id,
        point_count=1,
        bounds=(1.0, 2.0, 3.0, 1.0, 2.0, 3.0),
        roles=(PointRoleSummary("canopy_tree", 1),),
    )
    return point_set


class BlenderWorkerSupervisorTests(unittest.TestCase):
    def test_reconcile_ignores_process_owned_by_another_adapter(self) -> None:
        with self._environment("success") as environment:
            store, runtime, supervisor, _ = environment
            command = store.put_bytes(
                b'{"operation":"other-worker"}',
                media_type="application/json",
            )
            job = runtime.submit(
                JobCommandRef(
                    "verdflow.test.other-worker",
                    "1",
                    command.artifact_id,
                ),
                idempotency_key="other-worker-process",
            )
            claimed = runtime.claim_next(
                "other-worker",
                command_types=("verdflow.test.other-worker",),
            )
            assert claimed is not None
            child = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                inspector = SystemProcessInspector()
                current = inspector.current_identity()
                probe = inspector.probe(current.host, child.pid)
                self.assertEqual(probe.status, ProcessStatus.ALIVE)
                assert probe.identity is not None
                store.register_worker_process(
                    WorkerProcessInfo(
                        job_id=job.job_id,
                        attempt_number=claimed.lease.attempt_count,
                        adapter_id="verdflow-other-worker",
                        host=probe.identity.host,
                        pid=probe.identity.pid,
                        started_at_token=probe.identity.started_at_token,
                        status=WorkerProcessStatus.RUNNING,
                        started_at="2026-08-10T00:00:00+00:00",
                        heartbeat_at=None,
                        finished_at=None,
                        exit_code=None,
                    )
                )

                self.assertEqual(supervisor.reconcile_recorded_processes(), 0)
                self.assertIsNone(child.poll())
                self.assertEqual(
                    store.get_worker_process(job.job_id, 1).status,
                    WorkerProcessStatus.RUNNING,
                )
            finally:
                child.kill()
                child.wait(timeout=5.0)
                store.finish_worker_process(
                    job.job_id,
                    1,
                    status=WorkerProcessStatus.TERMINATED,
                    finished_at="2026-08-10T00:00:01+00:00",
                    exit_code=child.returncode,
                )

    def test_provider_neutral_region_command_runs_through_blender(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            root = Path(directory)
            with ProjectStore.create(
                root / "region-worker.verdflow",
                "Region worker",
            ) as store:
                source_artifact = store.put_bytes(
                    b"fake fbx bytes",
                    media_type="model/vnd.autodesk.fbx",
                )
                source = store.register_source(
                    kind=SourceKind.FBX,
                    original_name="scene.fbx",
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
                    importer_id="fixture-importer",
                    importer_version="1.7.0+blender-test",
                    coordinate_frame_id=frame.coordinate_frame_id,
                    scene_manifest_artifact_id=manifest.artifact_id,
                )
                runtime = JobRuntime(store)
                service = RegionExtractionService(
                    store,
                    runtime,
                    (
                        RegionExtractionProviderDefinition(
                            provider_id=BLENDER_WORKER_ADAPTER_ID,
                            provider_version="1",
                            command_type=BLENDER_EXTRACT_REGIONS_COMMAND,
                            command_version="1",
                        ),
                    ),
                )
                submitted = service.submit(
                    RegionExtractionRequest(
                        scene_revision_id=scene.scene_revision_id,
                        provider_id=BLENDER_WORKER_ADAPTER_ID,
                        rule_pack={
                            "schema": "verdflow.region-rule-pack",
                            "version": "1",
                            "rules": [
                                {
                                    "id": "grass",
                                    "match": {"material_names": ["grass"]},
                                }
                            ],
                        },
                    )
                )

                def fake_command(
                    control_dir: Path,
                    command_path: Path,
                ) -> list[str]:
                    return [
                        sys.executable,
                        str(FAKE_WORKER),
                        str(control_dir),
                        str(command_path),
                    ]

                supervisor = BlenderWorkerSupervisor(
                    runtime,
                    BlenderWorkerConfig(
                        blender_executable=Path(sys.executable),
                        runtime_root=root / "runtime",
                        poll_interval_seconds=0.02,
                        ready_timeout_seconds=5.0,
                        heartbeat_timeout_seconds=2.0,
                        execution_timeout_seconds=8.0,
                    ),
                    command_factory=fake_command,
                )
                completed = supervisor.run_once()

                assert completed is not None
                self.assertEqual(completed.status, JobStatus.SUCCEEDED)
                with store.open_artifact(submitted.command.artifact_id) as stream:
                    durable = json.load(stream)
                self.assertEqual(
                    durable["provider"]["id"],
                    BLENDER_WORKER_ADAPTER_ID,
                )
                self.assertNotIn("source_path", durable)
                self.assertNotIn("rule_pack_path", durable)
                self.assertFalse(any((root / "runtime").iterdir()))

    def test_provider_neutral_elevation_command_runs_through_blender(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            root = Path(directory)
            with ProjectStore.create(
                root / "elevation-worker.verdflow",
                "Elevation worker",
            ) as store:
                runtime = JobRuntime(store)
                generated = _generated_point_fixture(store, runtime)
                service = ElevationSamplingService(
                    store,
                    runtime,
                    (
                        ElevationProviderDefinition(
                            provider_id=BLENDER_WORKER_ADAPTER_ID,
                            provider_version="1",
                            command_type=BLENDER_SAMPLE_ELEVATION_COMMAND,
                            command_version="1",
                        ),
                    ),
                )
                submitted = service.submit(
                    ElevationSamplingRequest(
                        generated.point_set_id,
                        BLENDER_WORKER_ADAPTER_ID,
                        {
                            "ray_distance": 750.0,
                            "target_object_names": ["terrain"],
                        },
                    )
                )

                def fake_command(
                    control_dir: Path,
                    command_path: Path,
                ) -> list[str]:
                    return [
                        sys.executable,
                        str(FAKE_WORKER),
                        str(control_dir),
                        str(command_path),
                    ]

                supervisor = BlenderWorkerSupervisor(
                    runtime,
                    BlenderWorkerConfig(
                        blender_executable=Path(sys.executable),
                        runtime_root=root / "runtime",
                        poll_interval_seconds=0.02,
                        ready_timeout_seconds=5.0,
                        heartbeat_timeout_seconds=2.0,
                        execution_timeout_seconds=8.0,
                    ),
                    command_factory=fake_command,
                )

                completed = supervisor.run_once()

                assert completed is not None
                self.assertEqual(
                    completed.status,
                    JobStatus.SUCCEEDED,
                    msg=repr(completed.error),
                )
                resolved = PointSetImportService(store).finalize_elevation(
                    submitted.job_id
                )
                self.assertEqual(
                    resolved.parent_point_set_id,
                    generated.point_set_id,
                )
                self.assertEqual(resolved.hit_count, 1)
                self.assertEqual(resolved.min_z, 4.0)
                with store.open_artifact(submitted.command.artifact_id) as stream:
                    durable = json.load(stream)
                self.assertEqual(
                    durable["provider"]["id"],
                    BLENDER_WORKER_ADAPTER_ID,
                )
                self.assertIn("options", durable)
                self.assertNotIn("points_path", durable)
                self.assertNotIn("source_path", durable)
                self.assertFalse(any((root / "runtime").iterdir()))

    def test_asset_library_command_resolves_id_without_persisting_path(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            root = Path(directory)
            library = root / "vegetation.blend"
            library.write_bytes(b"synthetic blend bytes")
            registry = BlenderAssetLibraryRegistry(root / "registry")
            registration = registry.register("studio-vegetation", library)
            with ProjectStore.create(
                root / "asset-worker.verdflow",
                "Asset worker",
            ) as store:
                durable = {
                    "schema": "verdflow.blender-worker.command",
                    "version": "1",
                    "operation": "inspect_asset_library",
                    "library_id": registration.library_id,
                    "required_collection_names": ["VF_Tree"],
                    "emit_output": True,
                }
                command = store.put_bytes(
                    json.dumps(durable).encode("utf-8"),
                    media_type="application/vnd.verdflow.command+json",
                    schema_id="verdflow.blender-worker.command",
                    schema_version="1",
                )
                runtime = JobRuntime(store)
                runtime.submit(
                    JobCommandRef(
                        command_type=BLENDER_INSPECT_ASSET_LIBRARY_COMMAND,
                        command_version="1",
                        artifact_id=command.artifact_id,
                    ),
                    idempotency_key="inspect-asset-library",
                )

                def fake_command(
                    control_dir: Path,
                    command_path: Path,
                ) -> list[str]:
                    return [
                        sys.executable,
                        str(FAKE_WORKER),
                        str(control_dir),
                        str(command_path),
                    ]

                supervisor = BlenderWorkerSupervisor(
                    runtime,
                    BlenderWorkerConfig(
                        blender_executable=Path(sys.executable),
                        runtime_root=root / "runtime",
                        poll_interval_seconds=0.02,
                        ready_timeout_seconds=5.0,
                        heartbeat_timeout_seconds=2.0,
                        execution_timeout_seconds=8.0,
                    ),
                    command_factory=fake_command,
                    asset_library_registry=registry,
                )
                completed = supervisor.run_once()

                assert completed is not None
                self.assertEqual(completed.status, JobStatus.SUCCEEDED)
                with store.open_artifact(command.artifact_id) as stream:
                    persisted_command = json.load(stream)
                self.assertEqual(persisted_command, durable)
                self.assertNotIn("library_path", persisted_command)
                assert completed.result_artifact_id is not None
                with store.open_artifact(completed.result_artifact_id) as stream:
                    result = json.load(stream)
                output_id = result["output_artifacts"][0]["artifact_id"]
                with store.open_artifact(output_id) as stream:
                    facts = json.load(stream)
                self.assertTrue(facts["library_was_resolved"])
                self.assertEqual(facts["library_id"], "studio-vegetation")
                self.assertFalse(any((root / "runtime").iterdir()))

    def test_success_registers_result_and_process_provenance(self) -> None:
        with self._environment("success") as environment:
            store, runtime, supervisor, job = environment

            completed = supervisor.run_once()

            assert completed is not None
            self.assertEqual(completed.status, JobStatus.SUCCEEDED)
            self.assertIsNotNone(completed.result_artifact_id)
            process = store.get_worker_process(job.job_id, 1)
            self.assertEqual(process.status, WorkerProcessStatus.EXITED)
            self.assertEqual(process.exit_code, 0)
            with store.open_artifact(completed.result_artifact_id) as stream:
                result = json.load(stream)
            self.assertTrue(result["fake"])
            self.assertGreaterEqual(len(runtime.events(job.job_id)), 4)

    def test_scene_inputs_are_staged_and_outputs_become_artifacts(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            root = Path(directory)
            with ProjectStore.create(
                root / "scene-worker.verdflow",
                "Scene worker",
            ) as store:
                source_artifact = store.put_bytes(
                    b"fake fbx bytes",
                    media_type="model/vnd.autodesk.fbx",
                )
                from verdflow.contracts import SourceKind

                source = store.register_source(
                    kind=SourceKind.FBX,
                    original_name="scene.fbx",
                    artifact_id=source_artifact.artifact_id,
                )
                command = store.put_bytes(
                    json.dumps(
                        {
                            "schema": "verdflow.blender-worker.command",
                            "version": "1",
                            "operation": "inspect_scene",
                            "source_id": source.source_id,
                            "source_artifact_id": source.artifact_id,
                            "emit_output": True,
                            "emit_scene_cache": True,
                        }
                    ).encode("utf-8"),
                    media_type="application/vnd.verdflow.command+json",
                    schema_id="verdflow.blender-worker.command",
                    schema_version="1",
                )
                runtime = JobRuntime(store)
                job = runtime.submit(
                    JobCommandRef(
                        command_type=BLENDER_INSPECT_SCENE_COMMAND,
                        command_version="1",
                        artifact_id=command.artifact_id,
                    ),
                    idempotency_key="inspect-scene-output",
                )

                def fake_command(
                    control_dir: Path,
                    command_path: Path,
                ) -> list[str]:
                    return [
                        sys.executable,
                        str(FAKE_WORKER),
                        str(control_dir),
                        str(command_path),
                    ]

                supervisor = BlenderWorkerSupervisor(
                    runtime,
                    BlenderWorkerConfig(
                        blender_executable=Path(sys.executable),
                        runtime_root=root / "runtime",
                        scene_cache_root=root / "cache" / "blender-scenes",
                        poll_interval_seconds=0.02,
                        ready_timeout_seconds=5.0,
                        heartbeat_timeout_seconds=2.0,
                        execution_timeout_seconds=8.0,
                    ),
                    command_factory=fake_command,
                )

                completed = supervisor.run_once()

                assert completed is not None
                self.assertEqual(
                    completed.status,
                    JobStatus.SUCCEEDED,
                    msg=repr(completed.error),
                )
                assert completed.result_artifact_id is not None
                with store.open_artifact(
                    completed.result_artifact_id
                ) as stream:
                    result = json.load(stream)
                self.assertNotIn("output_files", result)
                self.assertEqual(len(result["output_artifacts"]), 1)
                output = result["output_artifacts"][0]
                with store.open_artifact(output["artifact_id"]) as stream:
                    facts = json.load(stream)
                self.assertTrue(facts["source_was_staged"])
                self.assertTrue(
                    (root / "cache" / "blender-scenes" / f"{job.job_id}.blend")
                    .is_file()
                )
                self.assertFalse(any((root / "runtime").iterdir()))

    def test_scene_revision_cache_is_injected_only_when_present(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            root = Path(directory)
            with ProjectStore.create(
                root / "scene-cache.verdflow",
                "Scene cache",
            ) as store:
                source_artifact = store.put_bytes(
                    b"fake fbx bytes",
                    media_type="model/vnd.autodesk.fbx",
                )
                source = store.register_source(
                    kind=SourceKind.FBX,
                    original_name="scene.fbx",
                    artifact_id=source_artifact.artifact_id,
                )
                frame = store.create_coordinate_frame(
                    name="Source scene world",
                    handedness="right-handed",
                    up_axis="+Z",
                    linear_unit="meter",
                    crs_kind=CoordinateReferenceKind.UNKNOWN,
                )
                producer_job_id = "job_scene_inspection"
                manifest = store.put_bytes(
                    json.dumps(
                        {
                            "schema": "verdflow.scene-manifest",
                            "version": "1",
                            "producer_job_id": producer_job_id,
                        }
                    ).encode("utf-8"),
                    media_type="application/json",
                )
                scene = store.create_scene_revision(
                    source_id=source.source_id,
                    importer_id="fixture-importer",
                    importer_version="1.7.0+blender-test",
                    coordinate_frame_id=frame.coordinate_frame_id,
                    scene_manifest_artifact_id=manifest.artifact_id,
                )
                runtime = JobRuntime(store)
                service = RegionExtractionService(
                    store,
                    runtime,
                    (
                        RegionExtractionProviderDefinition(
                            provider_id=BLENDER_WORKER_ADAPTER_ID,
                            provider_version="1",
                            command_type=BLENDER_EXTRACT_REGIONS_COMMAND,
                            command_version="1",
                        ),
                    ),
                )
                submitted = service.submit(
                    RegionExtractionRequest(
                        scene_revision_id=scene.scene_revision_id,
                        provider_id=BLENDER_WORKER_ADAPTER_ID,
                        rule_pack={
                            "schema": "verdflow.region-rule-pack",
                            "version": "1",
                            "rules": [
                                {
                                    "id": "grass",
                                    "match": {"material_names": ["grass"]},
                                }
                            ],
                        },
                    )
                )
                cache_root = root / "cache" / "blender-scenes"
                supervisor = BlenderWorkerSupervisor(
                    runtime,
                    BlenderWorkerConfig(
                        blender_executable=Path(sys.executable),
                        runtime_root=root / "runtime",
                        scene_cache_root=cache_root,
                    ),
                )
                claimed = runtime.claim_next(
                    "cache-test",
                    command_types=(BLENDER_EXTRACT_REGIONS_COMMAND,),
                )
                assert claimed is not None
                command = supervisor._load_command(
                    submitted.command.artifact_id,
                    BLENDER_EXTRACT_REGIONS_COMMAND,
                )
                control = root / "prepared"
                control.mkdir()
                prepared_without_cache = supervisor._prepare_command(
                    command,
                    BLENDER_EXTRACT_REGIONS_COMMAND,
                    control,
                    lease=claimed.lease,
                )
                self.assertNotIn("scene_cache_path", prepared_without_cache)

                expected = cache_root / f"{producer_job_id}.blend"
                expected.write_bytes(b"fake blend cache")
                prepared_with_cache = supervisor._prepare_command(
                    command,
                    BLENDER_EXTRACT_REGIONS_COMMAND,
                    control,
                    lease=claimed.lease,
                )
                self.assertEqual(
                    Path(prepared_with_cache["scene_cache_path"]),
                    expected,
                )
                self.assertIsNone(
                    supervisor._scene_cache_for_revision(
                        replace(
                            scene,
                            importer_version="1.5.0+blender-test",
                        )
                    )
                )

    def test_source_assembly_members_are_staged_with_matrices(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            root = Path(directory)
            with ProjectStore.create(
                root / "assembly-worker.verdflow",
                "Assembly worker",
            ) as store:
                sources = []
                for index in range(2):
                    artifact = store.put_bytes(
                        f"fake fbx {index}".encode("ascii"),
                        media_type="model/vnd.autodesk.fbx",
                    )
                    sources.append(
                        store.register_source(
                            kind=SourceKind.FBX,
                            original_name=f"member-{index}.fbx",
                            artifact_id=artifact.artifact_id,
                        )
                    )
                identity = (
                    1.0, 0.0, 0.0, 0.0,
                    0.0, 1.0, 0.0, 0.0,
                    0.0, 0.0, 1.0, 0.0,
                    0.0, 0.0, 0.0, 1.0,
                )
                translated = (
                    1.0, 0.0, 0.0, 25.0,
                    0.0, 1.0, 0.0, -4.0,
                    0.0, 0.0, 1.0, 2.0,
                    0.0, 0.0, 0.0, 1.0,
                )
                assembly = store.create_source_assembly(
                    original_name="two-members",
                    members=(
                        SourceAssemblyMemberSpec(
                            sources[0].source_id,
                            identity,
                        ),
                        SourceAssemblyMemberSpec(
                            sources[1].source_id,
                            translated,
                        ),
                    ),
                )
                command = store.put_bytes(
                    json.dumps(
                        {
                            "schema": "verdflow.blender-worker.command",
                            "version": "1",
                            "operation": "inspect_scene",
                            "source_id": assembly.source_id,
                            "source_artifact_id": assembly.artifact_id,
                            "emit_output": True,
                        }
                    ).encode("utf-8"),
                    media_type="application/vnd.verdflow.command+json",
                    schema_id="verdflow.blender-worker.command",
                    schema_version="1",
                )
                runtime = JobRuntime(store)
                runtime.submit(
                    JobCommandRef(
                        command_type=BLENDER_INSPECT_SCENE_COMMAND,
                        command_version="1",
                        artifact_id=command.artifact_id,
                    ),
                    idempotency_key="inspect-assembly",
                )

                def fake_command(
                    control_dir: Path,
                    command_path: Path,
                ) -> list[str]:
                    return [
                        sys.executable,
                        str(FAKE_WORKER),
                        str(control_dir),
                        str(command_path),
                    ]

                supervisor = BlenderWorkerSupervisor(
                    runtime,
                    BlenderWorkerConfig(
                        blender_executable=Path(sys.executable),
                        runtime_root=root / "runtime",
                        poll_interval_seconds=0.02,
                        ready_timeout_seconds=5.0,
                        heartbeat_timeout_seconds=2.0,
                        execution_timeout_seconds=8.0,
                    ),
                    command_factory=fake_command,
                )
                completed = supervisor.run_once()

                assert completed is not None
                self.assertEqual(completed.status, JobStatus.SUCCEEDED)
                assert completed.result_artifact_id is not None
                with store.open_artifact(completed.result_artifact_id) as stream:
                    result = json.load(stream)
                output_id = result["output_artifacts"][0]["artifact_id"]
                with store.open_artifact(output_id) as stream:
                    facts = json.load(stream)
                members = store.list_source_assembly_members(
                    assembly.source_id
                )
                self.assertTrue(facts["source_was_staged"])
                self.assertEqual(facts["source_member_count"], 2)
                self.assertEqual(
                    [row["source_member_id"] for row in facts["source_members"]],
                    [member.source_member_id for member in members],
                )
                self.assertEqual(
                    facts["source_members"][1][
                        "source_to_scene_matrix_row_major"
                    ],
                    list(translated),
                )
                staged_names = [
                    Path(row["source_path"]).name
                    for row in facts["source_members"]
                ]
                self.assertEqual(staged_names, ["0000.fbx", "0001.fbx"])
                control_names = {
                    Path(row["source_path"]).parents[2].name
                    for row in facts["source_members"]
                }
                self.assertEqual(len(control_names), 1)
                self.assertLessEqual(len(next(iter(control_names))), 23)

    def test_ready_timeout_terminates_process_and_persists_diagnostic(self) -> None:
        with self._environment(
            "no_ready",
            duration=2.0,
            ready_timeout=0.2,
        ) as environment:
            store, _, supervisor, job = environment

            failed = supervisor.run_once()

            assert failed is not None
            self.assertEqual(failed.status, JobStatus.FAILED)
            self.assertEqual(failed.error.code, "BLENDER_READY_TIMEOUT")
            self.assertIsNotNone(failed.error.diagnostic_artifact_id)
            process = store.get_worker_process(job.job_id, 1)
            self.assertEqual(
                process.status,
                WorkerProcessStatus.TERMINATED,
            )

    def test_heartbeat_timeout_terminates_unresponsive_worker(self) -> None:
        with self._environment(
            "no_heartbeat",
            duration=2.0,
            heartbeat_timeout=0.2,
        ) as environment:
            _, _, supervisor, _ = environment

            failed = supervisor.run_once()

            assert failed is not None
            self.assertEqual(failed.status, JobStatus.FAILED)
            self.assertEqual(
                failed.error.code,
                "BLENDER_HEARTBEAT_TIMEOUT",
            )

    def test_persistently_invalid_result_is_rejected(self) -> None:
        with self._environment("bad_result") as environment:
            _, _, supervisor, _ = environment

            failed = supervisor.run_once()

            assert failed is not None
            self.assertEqual(failed.status, JobStatus.FAILED)
            self.assertEqual(
                failed.error.code,
                "BLENDER_RESULT_INVALID",
            )
            self.assertIn(
                "result.json",
                failed.error.message,
            )

    def test_supervisor_renews_lease_while_blender_is_starting(self) -> None:
        with self._environment(
            "delayed_ready",
            duration=0.2,
            ready_timeout=2.0,
            lease_seconds=0.2,
        ) as environment:
            _, _, supervisor, _ = environment

            completed = supervisor.run_once()

            assert completed is not None
            self.assertEqual(completed.status, JobStatus.SUCCEEDED)

    def test_cancel_signal_is_persisted_by_supervisor_thread(self) -> None:
        holder: dict[str, BlenderWorkerSupervisor] = {}

        def request_cancel(process: object) -> None:
            holder["supervisor"].request_cancel(process.job_id)

        with self._environment(
            "success",
            duration=2.0,
            process_started=request_cancel,
        ) as environment:
            store, _, supervisor, job = environment
            holder["supervisor"] = supervisor

            cancelled = supervisor.run_once()

            assert cancelled is not None
            self.assertEqual(cancelled.status, JobStatus.CANCELLED)
            process = store.get_worker_process(job.job_id, 1)
            self.assertEqual(
                process.status,
                WorkerProcessStatus.TERMINATED,
            )

    def _environment(
        self,
        mode: str,
        *,
        duration: float = 0.3,
        ready_timeout: float = 2.0,
        heartbeat_timeout: float = 1.0,
        lease_seconds: float = 5.0,
        process_started: object | None = None,
    ) -> object:
        return SupervisorEnvironment(
            mode=mode,
            duration=duration,
            ready_timeout=ready_timeout,
            heartbeat_timeout=heartbeat_timeout,
            lease_seconds=lease_seconds,
            process_started=process_started,
        )


class SupervisorEnvironment:
    def __init__(
        self,
        *,
        mode: str,
        duration: float,
        ready_timeout: float,
        heartbeat_timeout: float,
        lease_seconds: float,
        process_started: object | None,
    ) -> None:
        self.mode = mode
        self.duration = duration
        self.ready_timeout = ready_timeout
        self.heartbeat_timeout = heartbeat_timeout
        self.lease_seconds = lease_seconds
        self.process_started = process_started
        self.temporary: tempfile.TemporaryDirectory[str] | None = None
        self.store: ProjectStore | None = None

    def __enter__(self) -> tuple[object, ...]:
        self.temporary = tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT)
        root = Path(self.temporary.name)
        self.store = ProjectStore.create(root / "worker.verdflow", "Worker")
        command = self.store.put_bytes(
            json.dumps(
                {
                    "schema": "verdflow.blender-worker.command",
                    "version": "1",
                    "operation": "probe",
                    "test_mode": self.mode,
                    "duration_seconds": self.duration,
                    "ready_delay_seconds": 0.5,
                }
            ).encode("utf-8"),
            media_type="application/vnd.verdflow.command+json",
            schema_id="verdflow.blender-worker.command",
            schema_version="1",
        )
        runtime = JobRuntime(
            self.store,
            lease_duration=timedelta(seconds=self.lease_seconds),
        )
        job = runtime.submit(
            JobCommandRef(
                command_type=BLENDER_PROBE_COMMAND,
                command_version="1",
                artifact_id=command.artifact_id,
            ),
            idempotency_key=f"{self.mode}-{id(self)}",
        )

        def fake_command(control_dir: Path, command_path: Path) -> list[str]:
            return [
                sys.executable,
                str(FAKE_WORKER),
                str(control_dir),
                str(command_path),
            ]

        config = BlenderWorkerConfig(
            blender_executable=Path(sys.executable),
            runtime_root=root / "runtime",
            poll_interval_seconds=0.02,
            ready_timeout_seconds=self.ready_timeout,
            heartbeat_timeout_seconds=self.heartbeat_timeout,
            execution_timeout_seconds=3.0,
            termination_grace_seconds=1.0,
        )
        supervisor = BlenderWorkerSupervisor(
            runtime,
            config,
            command_factory=fake_command,
            process_started=self.process_started,
        )
        return self.store, runtime, supervisor, job

    def __exit__(self, *_: object) -> None:
        if self.store is not None:
            self.store.close()
        if self.temporary is not None:
            self.temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
