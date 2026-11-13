from __future__ import annotations

from contextlib import closing
import json
import errno
import hashlib
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from threading import Event
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(path / "src")
    for group in ("packages", "adapters", "apps")
    for path in (ROOT / group).iterdir()
    if (path / "src").is_dir()
]

try:
    import numpy
    import rasterio
    from rasterio.transform import from_origin
except ImportError:
    numpy = None
    rasterio = None
    from_origin = None

from verdflow.contracts import (  # noqa: E402
    JobCommandRef,
    JobStatus,
    PointRoleSummary,
    SourceKind,
)
from verdflow.core import (  # noqa: E402
    ElevationSamplingRequest,
    JobRuntime,
    RegionSourceImportError,
    RegionSourceImportService,
    RegionSourceRequest,
)
from verdflow.core_host import (  # noqa: E402
    CoreHost,
    CoreHostConfig,
    GeospatialExtensionFactory,
)
from verdflow.system_host import (  # noqa: E402
    ProjectBusinessState,
    SourceIntakeRequest,
    SystemHost,
    SystemHostConfig,
)
from verdflow.geospatial_worker import (  # noqa: E402
    GEOSPATIAL_SAMPLE_ELEVATION_COMMAND,
    IMAGERY_PREPARE_REGIONS_COMMAND,
    GeospatialWorkerConfig,
    GeospatialWorkerSupervisor,
)
from verdflow.geospatial_worker.errors import GeospatialDataError  # noqa: E402
from verdflow.geospatial_worker.imagery_memory import ImageryMemoryPool  # noqa: E402
from verdflow.project_store import ProjectStore  # noqa: E402
from verdflow.worker_runtime import WindowsMemoryHeadroom  # noqa: E402
from verdflow.local_api import LocalApiConfig, LocalApiServer  # noqa: E402


class GeospatialWatchdogTests(unittest.TestCase):
    def test_observed_progress_can_exceed_watchdog_interval_but_stalled_work_cannot(self):
        module = "verdflow.geospatial_worker.supervisor"
        for stalls in (False, True):
            with self.subTest(stalls=stalls), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                with ProjectStore.create(root / "project", "Watchdog") as store:
                    runtime = JobRuntime(store)
                    supervisor = GeospatialWorkerSupervisor(runtime, GeospatialWorkerConfig(
                        root / "runtime", root / "cache", timeout_seconds=3, heartbeat_interval_seconds=1,
                    ))
                    artifact = store.put_bytes(b"{}", media_type="application/json")
                    job = runtime.submit(JobCommandRef(
                        GEOSPATIAL_SAMPLE_ELEVATION_COMMAND, "1", artifact.artifact_id,
                    ), idempotency_key="sample")
                    now = [0]
                    process = Mock(pid=42, returncode=None)
                    process.poll.side_effect = lambda: process.returncode
                    guard = Mock()
                    guard.popen_options.return_value = {}
                    guard.terminate.side_effect = lambda *_args, **_kwargs: setattr(process, "returncode", 1)
                    identity = SimpleNamespace(host="test", pid=42, started_at_token="fixture")
                    with (
                        patch(f"{module}.time", SimpleNamespace(monotonic=lambda: now[0])),
                        patch(f"{module}.ProcessTreeGuard", return_value=guard),
                        patch(f"{module}.subprocess.Popen", return_value=process),
                        patch.object(supervisor, "_child_identity", return_value=identity),
                        patch.object(supervisor, "_prepare_command", return_value={}),
                    ):
                        try:
                            self.assertIsNone(supervisor.run_once())
                            attempt, = supervisor.config.runtime_root.iterdir()
                            for number in range(1, 5):
                                now[0] += 2
                                (attempt / "progress.json").write_text(
                                    json.dumps(["sampling_elevation", number, 5]), encoding="ascii",
                                )
                                self.assertIsNone(supervisor.run_once())
                                self.assertIs(runtime.get(job.job_id).status, JobStatus.RUNNING)
                            if stalls:
                                now[0] += 4
                            else:
                                (attempt / "result.json").write_text(json.dumps({
                                    "status": "succeeded", "operation": "sample_elevation",
                                    "source_id": "fixture", "output_files": [],
                                }), encoding="ascii")
                                process.returncode = 0
                            completed = supervisor.run_once()
                            if stalls:
                                self.assertIs(completed.status, JobStatus.FAILED)
                                self.assertEqual(completed.error.code, "geo_worker_timeout")
                            else:
                                self.assertIs(completed.status, JobStatus.SUCCEEDED)
                        finally:
                            supervisor.close()


class ImageryMemoryAdmissionTests(unittest.TestCase):
    def test_memory_reserves_follow_smaller_and_larger_machines(self):
        mib = 1024**2
        for total, physical, commit, expected in (
            (8192, 1536, 3072, 2304),
            (16384, 2048, 6400, 3072),
            (65536, 32768, 30720, 26624),
            (65536, 3072, 30720, 2048),
        ):
            with self.subTest(total=total, physical=physical, commit=commit):
                snapshot = WindowsMemoryHeadroom(total * mib, physical * mib, commit * mib)
                pool = ImageryMemoryPool(lambda: snapshot)
                first = pool.try_reserve()
                self.assertEqual(first.limit_bytes, expected * mib)
                self.assertIsNone(pool.try_reserve())
                first.close()
                first.close()
                second = pool.try_reserve()
                self.assertEqual(second.limit_bytes, expected * mib)
                second.close()

    def test_low_headroom_is_not_replaced_by_the_fallback_quota(self):
        mib = 1024**2
        snapshot = WindowsMemoryHeadroom(8192 * mib, 2048 * mib, 1024 * mib)
        with self.assertRaises(GeospatialDataError) as error:
            ImageryMemoryPool(lambda: snapshot).try_reserve()
        self.assertEqual(error.exception.code, "imagery_memory_pressure")
        self.assertIn("256 MiB", str(error.exception))
        fallback = ImageryMemoryPool(lambda: None).try_reserve()
        self.assertEqual(fallback.limit_bytes, 3 * 1024**3)
        fallback.close()

    @unittest.skipUnless(os.name == "nt", "Windows imagery admission")
    def test_memory_pressure_fails_before_launch_and_leaves_project_unchanged(self):
        module = "verdflow.geospatial_worker.supervisor"
        mib = 1024**2
        pool = ImageryMemoryPool(lambda: WindowsMemoryHeadroom(8192 * mib, 2048 * mib, 1024 * mib))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with ProjectStore.create(root / "project", "Memory pressure") as store:
                runtime = JobRuntime(store)
                supervisor = GeospatialWorkerSupervisor(runtime, GeospatialWorkerConfig(
                    root / "runtime", root / "cache", enable_imagery=True,
                ))
                command = store.put_bytes(b"{}", media_type="application/json")
                job = runtime.submit(JobCommandRef(
                    IMAGERY_PREPARE_REGIONS_COMMAND, "1", command.artifact_id,
                ), idempotency_key="memory-pressure")
                with (
                    patch(f"{module}._IMAGERY_MEMORY_POOL", pool),
                    patch(f"{module}.subprocess.Popen") as launch,
                    patch.object(supervisor, "_prepare_command", return_value={}),
                ):
                    try:
                        completed = supervisor.run_once()
                        launch.assert_not_called()
                        self.assertEqual(completed.job_id, job.job_id)
                        self.assertIs(completed.status, JobStatus.FAILED)
                        self.assertEqual(completed.error.code, "imagery_memory_pressure")
                        self.assertTrue(completed.error.retryable)
                        self.assertIsNone(completed.result_artifact_id)
                        self.assertEqual(store.list_active_worker_processes(), ())
                        self.assertIsNone(store.project_workflow_state().region_set_id)
                        self.assertEqual(list(supervisor.config.runtime_root.iterdir()), [])
                    finally:
                        supervisor.close()

    @unittest.skipUnless(os.name == "nt", "Windows imagery admission")
    def test_waiting_projects_can_cancel_and_launch_only_after_capacity_returns(self):
        module = "verdflow.geospatial_worker.supervisor"
        gib = 1024**3
        snapshot = WindowsMemoryHeadroom(64 * gib, 32 * gib, 30 * gib)
        pool = ImageryMemoryPool(lambda: snapshot)
        now = [0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                ProjectStore.create(root / "first", "First") as first_store,
                ProjectStore.create(root / "second", "Second") as second_store,
            ):
                def create(store, name):
                    return GeospatialWorkerSupervisor(JobRuntime(store), GeospatialWorkerConfig(
                        root / f"{name}-runtime", root / f"{name}-cache",
                        timeout_seconds=3, heartbeat_interval_seconds=1, enable_imagery=True,
                    ))

                def submit(supervisor, key):
                    artifact = supervisor.runtime.store.put_bytes(b"{}", media_type="application/json")
                    return supervisor.runtime.submit(JobCommandRef(
                        IMAGERY_PREPARE_REGIONS_COMMAND, "1", artifact.artifact_id,
                    ), idempotency_key=key)

                first, second = create(first_store, "first"), create(second_store, "second")
                process_a, process_b = Mock(pid=42, returncode=None), Mock(pid=43, returncode=None)
                for process in (process_a, process_b):
                    process.poll.side_effect = lambda child=process: child.returncode
                guard = Mock()
                guard.popen_options.return_value = {}
                guard.terminate.side_effect = lambda child, **_: setattr(child, "returncode", 1)
                command = {"mask_checkpoint": str(root / "unused.tif")}
                with (
                    patch(f"{module}._IMAGERY_MEMORY_POOL", pool),
                    patch(f"{module}.time", SimpleNamespace(monotonic=lambda: now[0])),
                    patch(f"{module}.ProcessTreeGuard", return_value=guard) as guarded,
                    patch(f"{module}.subprocess.Popen", side_effect=(process_a, process_b)) as launch,
                    patch.object(GeospatialWorkerSupervisor, "_prepare_command", return_value=command),
                    patch.object(GeospatialWorkerSupervisor, "_child_identity", side_effect=lambda pid: SimpleNamespace(
                        host="test", pid=pid, started_at_token="fixture",
                    )),
                ):
                    try:
                        submit(first, "running")
                        self.assertIsNone(first.run_once())
                        guarded.assert_called_once_with(memory_limit_bytes=26 * gib)
                        waiting = submit(second, "waiting")
                        self.assertIsNone(second.run_once())
                        now[0] += 10  # Waiting is outside the native watchdog window.
                        self.assertIsNone(second.run_once())
                        self.assertIs(second.runtime.get(waiting.job_id).status, JobStatus.RUNNING)
                        self.assertEqual(launch.call_count, 1)
                        second.request_cancel(waiting.job_id)
                        self.assertIs(second.run_once().status, JobStatus.CANCELLED)
                        self.assertEqual(list(second.config.runtime_root.iterdir()), [])
                        submit(second, "after-cancel")
                        self.assertIsNone(second.run_once())
                        self.assertEqual(launch.call_count, 1)
                        attempt, = first.config.runtime_root.iterdir()
                        (attempt / "result.json").write_text(json.dumps({
                            "status": "succeeded", "operation": "prepare_imagery_regions",
                            "source_id": "fixture", "output_files": [],
                        }), encoding="ascii")
                        process_a.returncode = 0
                        self.assertIs(first.run_once().status, JobStatus.SUCCEEDED)
                        self.assertIsNone(second.run_once())
                        self.assertEqual(launch.call_count, 2)
                    finally:
                        first.close()
                        second.close()
                last = pool.try_reserve()
                self.assertEqual(last.limit_bytes, 26 * gib)
                last.close()


@unittest.skipIf(
    numpy is None or rasterio is None or not os.environ.get("VERDFLOW_GEOSPATIAL_TEST_PYTHON"),
    "geospatial process test environment is not configured",
)
class GeospatialVerticalTests(unittest.TestCase):
    def test_external_worker_finalizes_scene_and_regions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            geojson = root / "regions.geojson"
            geojson.write_text(
                json.dumps(
                    {
                        "type": "Polygon",
                        "coordinates": [
                            [[0.25, 0.25], [2.75, 0.25], [2.75, 2.75], [0.25, 2.75], [0.25, 0.25]]
                        ],
                    }
                ),
                encoding="utf-8",
            )
            tif = root / "elevation.tif"
            with rasterio.open(
                tif,
                "w",
                driver="GTiff",
                width=3,
                height=3,
                count=1,
                dtype="float32",
                transform=from_origin(0, 3, 1, 1),
                nodata=-9999.0,
            ) as dataset:
                dataset.write(numpy.full((3, 3), 12.5, dtype="float32"), 1)
            project = root / "project.verdflow"
            factory = GeospatialExtensionFactory(
                runtime_root=root / "runtime",
                cache_root=root / "cache",
                python_executable=Path(os.environ["VERDFLOW_GEOSPATIAL_TEST_PYTHON"]),
            )
            with CoreHost.create(
                CoreHostConfig(
                    project_path=project,
                    enable_blender_worker=False,
                    enable_project_maintenance=False,
                    poll_interval_seconds=0.01,
                ),
                project_name="Geospatial vertical",
                extension_factories=(factory,),
            ) as host:
                vector = host.add_source_file(geojson, kind=SourceKind.GEOJSON)
                elevation = host.add_source_file(tif, kind=SourceKind.GEOTIFF)
                assembly = host.create_identity_assembly(
                    original_name="regions + elevation",
                    source_ids=(vector.source_id, elevation.source_id),
                )

                outcome = host.prepare_region_source(
                    RegionSourceRequest(
                        source_id=assembly.source_id,
                        provider_id="verdflow.geospatial",
                    ),
                    timeout_seconds=20.0,
                )

                self.assertEqual(outcome.region_set.region_count, 1)
                self.assertEqual(
                    outcome.region_set.scene_revision_id,
                    outcome.scene_revision.scene_revision_id,
                )
                self.assertEqual(
                    host.store.list_regions(outcome.region_set.region_set_id)[0].footprint_area_xy,
                    6.25,
                )
                generated = _generated_point_set(host, outcome)
                sampled = host.sample_elevation(
                    ElevationSamplingRequest(
                        parent_point_set_id=generated.point_set_id,
                        provider_id="verdflow.geospatial",
                    ),
                    timeout_seconds=20.0,
                )
                with host.store.open_artifact(sampled.point_set.point_artifact_id) as stream:
                    resolved = json.load(stream)
                self.assertEqual(
                    resolved["points"][0]["resolved_position"],
                    [0.5, 2.5, 12.5],
                )

    def test_system_host_exposes_direct_regions_as_ready_to_plant(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            geojson, tif = _write_inputs(root)
            with SystemHost(
                SystemHostConfig(library_path=root / "library")
            ) as system:
                project = system.create_project(
                    root / "project.verdflow",
                    project_name="Geospatial system route",
                    enable_blender_worker=False,
                    enable_geospatial_worker=True,
                )
                vector = system.add_source_file(
                    project.session_id,
                    geojson,
                    kind=SourceKind.GEOJSON,
                )
                elevation = system.add_source_file(
                    project.session_id,
                    tif,
                    kind=SourceKind.GEOTIFF,
                )
                assembly = system.create_identity_source_assembly(
                    project.session_id,
                    original_name="regions + elevation",
                    source_ids=(vector.source_id, elevation.source_id),
                )
                job = system.submit_region_source(
                    project.session_id,
                    RegionSourceRequest(
                        source_id=assembly.source_id,
                        provider_id="verdflow.geospatial",
                    ),
                    idempotency_key="system-geospatial",
                )
                deadline = time.monotonic() + 20.0
                while job.status not in {
                    JobStatus.SUCCEEDED,
                    JobStatus.FAILED,
                    JobStatus.CANCELLED,
                }:
                    if time.monotonic() >= deadline:
                        self.fail("Geospatial SystemHost Job timed out")
                    time.sleep(0.02)
                    job = system.get_job(project.session_id, job.job_id)
                self.assertEqual(job.status, JobStatus.SUCCEEDED)
                scene, regions = system.finalize_region_source(
                    project.session_id,
                    job.job_id,
                )

                summary = system.get_project_business_summary(
                    project.session_id
                )

                self.assertEqual(summary.state, ProjectBusinessState.READY_TO_PLANT)
                self.assertEqual(
                    summary.scene_revision_id,
                    scene.scene_revision_id,
                )
                self.assertEqual(summary.region_set_id, regions.region_set_id)
                self.assertIsNone(summary.scene_preparation_execution_id)

    def test_source_intake_http_accepts_before_copy_and_keeps_failure_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vector, tif = _write_inputs(root)
            config = SystemHostConfig(library_path=root / "library", project_call_timeout_seconds=2)
            token = "local-source-intake-test-management-token"
            path = root / "project.verdflow"
            with SystemHost(config) as system, LocalApiServer(system, LocalApiConfig(management_token=token)) as api:
                project = system.create_project(path, project_name="Source intake", enable_blender_worker=False,
                    enable_geospatial_worker=True)
                session = project.session_id
                route = f"{api.base_url}/api/v1/projects/{session}"

                def request(suffix, body=None, *, authorized=True):
                    headers = {"Authorization": f"Bearer {token}"} if authorized else {}
                    req = Request(route + suffix, headers=headers, data=json.dumps(body).encode() if body is not None else None)
                    with urlopen(req, timeout=5) as response:
                        return response.status, json.load(response)

                def wait_for(predicate):
                    deadline = time.monotonic() + 20
                    while time.monotonic() < deadline:
                        value = predicate()
                        if value:
                            return value
                        time.sleep(0.05)
                    self.fail("Source intake did not reach expected state")

                entered, release = Event(), Event()
                real_copy = ProjectStore.put_file

                def fail_terrain(store, source, **kwargs):
                    if source == tif.resolve():
                        entered.set()
                        self.assertTrue(release.wait(8))
                        raise OSError(errno.ENOSPC, "fixture")
                    return real_copy(store, source, **kwargs)

                body = {"workflow": "vector", "source_path": str(vector), "elevation_path": str(tif), "idempotency_key": "fixture"}
                with self.assertRaises(HTTPError) as denied:
                    request("/source-intake", body, authorized=False)
                self.assertEqual(denied.exception.code, 403)
                with patch.object(ProjectStore, "put_file", fail_terrain):
                    status, job = request("/source-intake", body)
                    self.assertEqual(status, 202)
                    try:
                        self.assertTrue(entered.wait(5))
                        self.assertEqual(request("/summary")[1]["execution_kind"], "source_intake")
                        self.assertEqual(request("/source-intake", body)[1]["job_id"], job["job_id"])
                        with closing(sqlite3.connect((path / "project.db").as_uri() + "?mode=ro", uri=True)) as db:
                            self.assertEqual(db.execute("SELECT count(*) FROM sources").fetchone()[0], 0)
                    finally:
                        release.set()
                    wait_for(lambda: system.get_job(session, job["job_id"]).status is JobStatus.FAILED)
                failed = system.get_job(session, job["job_id"])
                self.assertEqual(failed.error.code, "source_disk_full")
                self.assertIsNone(system.get_project_business_summary(session).source_id)
                copies = []

                def track_retry(store, source, **kwargs):
                    copies.append(source)
                    return real_copy(store, source, **kwargs)

                with patch.object(ProjectStore, "put_file", track_retry):
                    status, retry = request(f"/source-intake/{job['job_id']}/retry", {})
                    self.assertEqual(status, 202)
                    self.assertNotEqual(retry["job_id"], job["job_id"])
                    wait_for(lambda: system.get_project_business_summary(session).state is ProjectBusinessState.READY_TO_PLANT)
                self.assertNotIn(vector.resolve(), copies)
                self.assertIn(tif.resolve(), copies)
                finished = system.get_job(session, retry["job_id"])
                self.assertIs(finished.status, JobStatus.SUCCEEDED)

                def verify(store):
                    with store.open_artifact(finished.command.artifact_id) as stream:
                        command = json.load(stream)
                        self.assertNotIn("source_path", command)
                        self.assertNotIn(str(root), json.dumps(command))
                    with store.open_artifact(finished.result_artifact_id) as stream:
                        result = json.load(stream)
                    copied = [store.get_source(source_id) for source_id in result["source_ids"]]
                    self.assertEqual([item.kind for item in copied], [SourceKind.GEOJSON, SourceKind.GEOTIFF])
                    self.assertEqual([item.content_hash for item in copied], [hashlib.sha256(file.read_bytes()).hexdigest() for file in (vector, tif)])
                    return store.get_job(result["preparation_id"])

                following = system._project_runtime(session).query(verify, timeout_seconds=2)
                self.assertIs(following.status, JobStatus.SUCCEEDED)
                self.assertEqual(list((config.resolved_system_path() / "runtime" / "source-intake").glob("*.json")), [])

    def test_source_intake_reopens_queued_work_and_rejects_changed_input(self) -> None:
        for changed in (False, True):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                vector, tif = _write_inputs(root)
                project_path = root / "project.verdflow"
                config = SystemHostConfig(root / "library", project_idle_cycle_seconds=600)
                with SystemHost(config) as system:
                    project = system.create_project(project_path, project_name="Source intake restart", enable_blender_worker=False,
                        enable_geospatial_worker=True)
                    job = system.submit_source_intake(project.session_id, SourceIntakeRequest("vector", vector, tif),
                        idempotency_key="restart-fixture")
                    self.assertIs(job.status, JobStatus.QUEUED)
                if changed:
                    with vector.open("ab") as output:
                        output.write(b"\n")
                with SystemHost(SystemHostConfig(config.library_path)) as reopened:
                    session = reopened.open_project(project_path, enable_blender_worker=False, enable_geospatial_worker=True).session_id
                    deadline = time.monotonic() + 15
                    while time.monotonic() < deadline:
                        result = reopened.get_job(session, job.job_id)
                        if result.status in {JobStatus.SUCCEEDED, JobStatus.FAILED}:
                            break
                        time.sleep(0.05)
                    self.assertIs(result.status, JobStatus.FAILED if changed else JobStatus.SUCCEEDED)
                    if changed:
                        self.assertEqual(result.error.code, "source_file_changed")
                        with closing(sqlite3.connect((project_path / "project.db").as_uri() + "?mode=ro", uri=True)) as db:
                            self.assertEqual(db.execute("SELECT count(*) FROM sources").fetchone()[0], 0)

    def test_source_intake_cancellation_before_publication_keeps_project_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vector, tif = _write_inputs(root)
            path = root / "project.verdflow"
            with SystemHost(SystemHostConfig(root / "library")) as system:
                session = system.create_project(path, project_name="Source intake cancellation", enable_blender_worker=False,
                    enable_geospatial_worker=True).session_id
                entered, release = Event(), Event()
                real_copy = ProjectStore.put_file

                def pause_copy(store, source, **kwargs):
                    if source == tif.resolve():
                        entered.set()
                        self.assertTrue(release.wait(5))
                    return real_copy(store, source, **kwargs)

                with patch.object(ProjectStore, "put_file", pause_copy):
                    job = system.submit_source_intake(session, SourceIntakeRequest("vector", vector, tif), idempotency_key="cancel")
                    try:
                        self.assertTrue(entered.wait(5))
                        self.assertTrue(system.request_job_cancel(session, job.job_id).accepted)
                    finally:
                        release.set()
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        job = system.get_job(session, job.job_id)
                        if job.status in {JobStatus.CANCELLED, JobStatus.FAILED, JobStatus.SUCCEEDED}:
                            break
                        time.sleep(0.05)
                self.assertIs(job.status, JobStatus.CANCELLED)
                with closing(sqlite3.connect((path / "project.db").as_uri() + "?mode=ro", uri=True)) as db:
                    self.assertEqual(db.execute("SELECT count(*) FROM sources").fetchone()[0], 0)

    def test_shapefile_publication_is_background_atomic_and_cancellable(self) -> None:
        import shapefile

        for outcome in ("success", "failure", "cancel"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                _, tif = _write_inputs(root)
                shp = root / "regions.shp"
                with shapefile.Writer(str(shp), shapeType=shapefile.POLYGON) as writer:
                    writer.field("label", "C")
                    writer.poly([[[0.25, 0.25], [0.25, 2.75], [2.75, 2.75], [2.75, 0.25], [0.25, 0.25]]])
                    writer.record("Fixture")
                original = RegionSourceImportService.finalize
                waiting, release = Event(), Event()

                def gated_finalize(importer, job_id, **kwargs):
                    result = original(importer, job_id, **kwargs)
                    waiting.set()
                    if not release.wait(10):
                        raise AssertionError("Test did not release publication")
                    if outcome == "failure":
                        raise RegionSourceImportError("injected publication failure")
                    return result

                with (
                    patch.object(RegionSourceImportService, "finalize", gated_finalize),
                    SystemHost(SystemHostConfig(library_path=root / "library")) as system,
                ):
                    project = system.create_project(
                        root / "project.verdflow", project_name="Shapefile background",
                        enable_blender_worker=False, enable_geospatial_worker=True,
                    )
                    session = project.session_id
                    vector = system.add_source_file(session, shp, kind=SourceKind.SHAPEFILE)
                    elevation = system.add_source_file(session, tif, kind=SourceKind.GEOTIFF)
                    assembly = system.create_identity_source_assembly(
                        session, original_name="regions + elevation",
                        source_ids=(vector.source_id, elevation.source_id),
                    )
                    request = RegionSourceRequest(assembly.source_id, "verdflow.geospatial", auto_publish=True)
                    try:
                        token = "geospatial-publication-management-token"
                        with LocalApiServer(system, LocalApiConfig(management_token=token)) as server:
                            submit = Request(
                                f"{server.base_url}/api/v1/projects/{session}/region-sources",
                                method="POST",
                                data=json.dumps({
                                    "source_id": assembly.source_id, "provider_id": request.provider_id,
                                    "auto_publish": True, "idempotency_key": "background",
                                }).encode(),
                                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                            )
                            with urlopen(submit, timeout=10) as response:
                                self.assertEqual(response.status, 202)
                                job_id = json.load(response)["job_id"]
                        job = system.get_job(session, job_id)
                        self.assertTrue(waiting.wait(15), system.get_job(session, job.job_id))
                        visible = system.get_project_business_summary(session)
                        self.assertEqual(visible.state, ProjectBusinessState.RUNNING)
                        self.assertEqual(visible.execution_id, job.job_id)
                        self.assertEqual(visible.execution_stage, "publishing_regions")
                        self.assertIsNone(visible.region_set_id)
                        if outcome == "cancel":
                            self.assertTrue(system.request_job_cancel(session, job.job_id).accepted)
                    finally:
                        release.set()

                    deadline = time.monotonic() + 15
                    while time.monotonic() < deadline:
                        finished = system.get_job(session, job.job_id)
                        if finished.status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}:
                            break
                        time.sleep(0.03)
                    self.assertIs(finished.status, {
                        "success": JobStatus.SUCCEEDED, "failure": JobStatus.FAILED, "cancel": JobStatus.CANCELLED,
                    }[outcome])
                    if outcome == "failure":
                        self.assertEqual(finished.error.code, "geo_region_publication_failed")
                    if outcome != "success":
                        self.assertIsNone(finished.result_artifact_id)
                    if outcome == "success":
                        duplicate = system.submit_region_source(session, request, idempotency_key="background")
                        self.assertEqual(duplicate.job_id, job.job_id)
                    summary = system.get_project_business_summary(session)
                    self.assertIs(summary.state, {
                        "success": ProjectBusinessState.READY_TO_PLANT,
                        "failure": ProjectBusinessState.FAILED,
                        "cancel": ProjectBusinessState.CANCELLED,
                    }[outcome])
                    self.assertEqual(summary.region_set_id is not None, outcome == "success")
                    self.assertEqual(summary.scene_revision_id is not None, outcome == "success")
                with ProjectStore.open(root / "project.verdflow") as store:
                    self.assertEqual(len(store.list_scene_revisions()), int(outcome == "success"))
                    self.assertEqual(store.get_region_set_by_job(job.job_id) is not None, outcome == "success")


def _write_inputs(root: Path) -> tuple[Path, Path]:
    geojson = root / "regions.geojson"
    geojson.write_text(
        json.dumps(
            {
                "type": "Polygon",
                "coordinates": [
                    [
                        [0.25, 0.25],
                        [2.75, 0.25],
                        [2.75, 2.75],
                        [0.25, 2.75],
                        [0.25, 0.25],
                    ]
                ],
            }
        ),
        encoding="utf-8",
    )
    tif = root / "elevation.tif"
    with rasterio.open(
        tif,
        "w",
        driver="GTiff",
        width=3,
        height=3,
        count=1,
        dtype="float32",
        transform=from_origin(0, 3, 1, 1),
        nodata=-9999.0,
    ) as dataset:
        dataset.write(numpy.full((3, 3), 12.5, dtype="float32"), 1)
    return geojson, tif


def _generated_point_set(host: CoreHost, outcome, *, region_id="region_0001_001", xy=(0.5, 2.5)):
    position = [*xy, 0.0]
    points = {
        "schema": "verdflow.points",
        "version": "2",
        "strategy": {"strategy_id": "fixture", "strategy_version": "1"},
        "coordinate_space": "source_scene_world",
        "offset_applied": False,
        "kind": "generated",
        "resolution_policy": "generated_position",
        "point_count": 1,
        "points": [
            {
                "id": "point_1",
                "region_id": region_id,
                "role_key": "plant",
                "generated_position": position,
                "terrain_sample": None,
                "resolved_position": position,
                "rotation_quaternion_xyzw": [0.0, 0.0, 0.0, 1.0],
            }
        ],
    }
    point_artifact = host.store.put_bytes(
        json.dumps(points).encode("utf-8"),
        media_type="application/vnd.verdflow.points+json",
        schema_id="verdflow.points",
        schema_version="2",
    )
    command = host.store.put_bytes(b"{}", media_type="application/json")
    job = host.runtime.submit(
        JobCommandRef(
            command_type="fixture.generate",
            command_version="1",
            artifact_id=command.artifact_id,
        ),
        idempotency_key="fixture-generate",
    )
    claimed = host.runtime.claim_next(
        "fixture-generator",
        command_types=("fixture.generate",),
    )
    assert claimed is not None
    result = host.store.put_bytes(b"{}", media_type="application/json")
    host.runtime.succeed(claimed.lease, result_artifact_id=result.artifact_id)
    _, point_set = host.store.create_generated_point_set(
        scene_revision_id=outcome.scene_revision.scene_revision_id,
        region_set_id=outcome.region_set.region_set_id,
        producer_job_id=job.job_id,
        strategy_id="fixture",
        strategy_version="1",
        strategy_package_hash="sha256:fixture",
        generator_id="fixture",
        generator_version="1",
        random_seed=1,
        input_hash="sha256:fixture-input",
        coordinate_frame_id=outcome.scene_revision.coordinate_frame_id,
        point_artifact_id=point_artifact.artifact_id,
        point_count=1,
        bounds=(*position, *position),
        roles=(PointRoleSummary(role_key="plant", point_count=1),),
    )
    return point_set


if __name__ == "__main__":
    unittest.main()
