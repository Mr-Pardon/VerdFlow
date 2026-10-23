from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "worker-runtime" / "src"),
    str(ROOT / "packages" / "planting" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
    str(ROOT / "packages" / "core" / "src"),
    str(ROOT / "packages" / "library-store" / "src"),
    str(ROOT / "adapters" / "strategy-worker" / "src"),
]

from verdflow.contracts import (  # noqa: E402
    CoordinateReferenceKind,
    JobCommandRef,
    JobStatus,
    PLANTING_GENERATE_POINTS_COMMAND,
    PLANTING_GENERATION_COMMAND_SCHEMA,
    PLANTING_GENERATION_COMMAND_VERSION,
    RegionSummary,
    SourceKind,
    WorkerProcessStatus,
)
from verdflow.core import (  # noqa: E402
    JobRuntime,
    StrategyExecutionAuthorizer,
)
from verdflow.library_store import LibraryStore  # noqa: E402
from verdflow.project_store import (  # noqa: E402
    ProcessStatus,
    ProjectStore,
    SystemProcessInspector,
)
from verdflow.strategy_worker import (  # noqa: E402
    StrategyWorkerConfig,
    StrategyWorkerSupervisor,
    StrategyWorkerSupervisorConfig,
    inspect_strategy_package,
)


WORK_ROOT = ROOT / "tests" / ".tmp" / "strategy-worker-recovery"
RESULT_PATH = (
    ROOT
    / "tests"
    / "integration"
    / "results"
    / "strategy-worker-recovery.json"
)
CRASH_HOST = (
    ROOT
    / "tests"
    / "integration"
    / "helpers"
    / "strategy_worker_crash_host.py"
)
SOURCE_STRATEGY = ROOT / "strategy-packs" / "universal-street-tree"


def main() -> int:
    _reset_work_root()
    project_path = WORK_ROOT / "recovery.verdflow"
    library_path = WORK_ROOT / "library"
    runtime_path = WORK_ROOT / "runtime"
    launch_path = WORK_ROOT / "launch.json"
    strategy_root = _prepare_slow_strategy()

    with LibraryStore.open(library_path) as library:
        package = inspect_strategy_package(strategy_root)
        library.install_verified_strategy(
            package.root,
            package_id=package.package_id,
            package_version=package.package_version,
            package_hash=package.package_hash,
            display_name="Strategy recovery fixture",
        )
        library.trust_strategy(package.package_hash, decided_by="probe")

    with ProjectStore.create(project_path, "Strategy Recovery") as store:
        runtime = JobRuntime(store, lease_duration=timedelta(seconds=1))
        scene_id, region_set_id = _create_scene_and_regions(store, runtime)
        command = store.put_bytes(
            json.dumps(
                {
                    "schema": PLANTING_GENERATION_COMMAND_SCHEMA,
                    "version": PLANTING_GENERATION_COMMAND_VERSION,
                    "operation": "generate_points",
                    "scene_revision_id": scene_id,
                    "region_set_id": region_set_id,
                    "selected_region_ids": ["street_region"],
                    "strategy": {
                        "id": package.package_id,
                        "version": package.package_version,
                        "package_hash": package.package_hash,
                    },
                    "random_seed": 20260810,
                    "args": {},
                },
                sort_keys=True,
            ).encode("utf-8"),
            media_type="application/vnd.verdflow.command+json",
            schema_id=PLANTING_GENERATION_COMMAND_SCHEMA,
            schema_version=PLANTING_GENERATION_COMMAND_VERSION,
        )
        job = runtime.submit(
            JobCommandRef(
                command_type=PLANTING_GENERATE_POINTS_COMMAND,
                command_version=PLANTING_GENERATION_COMMAND_VERSION,
                artifact_id=command.artifact_id,
            ),
            idempotency_key="strategy-crash-recovery",
            max_attempts=2,
        )

    host = subprocess.Popen(
        [
            sys.executable,
            str(CRASH_HOST),
            str(project_path),
            str(library_path),
            str(runtime_path),
            str(launch_path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    launch = _wait_for_launch(launch_path, timeout=20.0)
    child_pid = int(launch["pid"])
    child_token = str(launch["started_at_token"])
    host.kill()
    host.wait(timeout=5.0)

    inspector = SystemProcessInspector()
    child_reaped = _wait_for_process_death(
        inspector,
        str(launch["host"]),
        child_pid,
        child_token,
        timeout=5.0,
    )
    time.sleep(1.3)

    with (
        LibraryStore.open(library_path) as library,
        ProjectStore.open(project_path) as store,
    ):
        runtime = JobRuntime(store, lease_duration=timedelta(seconds=1))
        supervisor = StrategyWorkerSupervisor(
            runtime,
            StrategyExecutionAuthorizer(library, library),
            library,
            StrategyWorkerSupervisorConfig(
                worker=StrategyWorkerConfig(
                    timeout_seconds=30.0,
                    poll_interval_seconds=0.05,
                ),
                heartbeat_interval_seconds=0.1,
                runtime_root=runtime_path,
            ),
            worker_id="strategy-recovery-worker",
        )
        reconciled = supervisor.reconcile_recorded_processes()
        recovery = runtime.recover_expired()
        queued = runtime.get(job.job_id)
        completed = supervisor.run_once()
        assert completed is not None
        attempts = runtime.attempts(job.job_id)
        first_process = store.get_worker_process(job.job_id, 1)
        second_process = store.get_worker_process(job.job_id, 2)

        checks = {
            "core_process_killed": host.returncode is not None,
            "strategy_child_reaped": child_reaped,
            "stale_process_reconciled": (
                reconciled == 1
                and first_process.status
                in {
                    WorkerProcessStatus.LOST,
                    WorkerProcessStatus.TERMINATED,
                }
            ),
            "expired_attempt_requeued": (
                recovery.requeued_count == 1
                and queued.status is JobStatus.QUEUED
            ),
            "second_attempt_succeeded": (
                completed.status is JobStatus.SUCCEEDED
                and len(attempts) == 2
                and second_process.status is WorkerProcessStatus.EXITED
            ),
            "stale_runtime_cleaned": (
                runtime_path.is_dir() and not any(runtime_path.iterdir())
            ),
        }
        report = {
            "schema": "verdflow.strategy-worker-recovery-probe",
            "version": "1",
            "passed": all(checks.values()),
            "checks": checks,
            "job_id": job.job_id,
            "host_exit_code": host.returncode,
            "child_pid": child_pid,
            "reconciled_process_count": reconciled,
            "recovery": {
                "interrupted_count": recovery.interrupted_count,
                "requeued_count": recovery.requeued_count,
            },
            "attempt_statuses": [
                attempt.status.value for attempt in attempts
            ],
            "process_statuses": [
                first_process.status.value,
                second_process.status.value,
            ],
        }
        RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESULT_PATH.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["passed"] else 1


def _reset_work_root() -> None:
    if WORK_ROOT.exists():
        shutil.rmtree(WORK_ROOT)
    WORK_ROOT.mkdir(parents=True)


def _prepare_slow_strategy() -> Path:
    destination = WORK_ROOT / "slow-strategy"
    shutil.copytree(
        SOURCE_STRATEGY,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    generator_path = destination / "generator.py"
    source = generator_path.read_text(encoding="utf-8")
    marker = "def generate(context: dict[str, Any]) -> dict[str, Any]:\n"
    if marker not in source:
        raise RuntimeError("Strategy fixture entry point was not found")
    source = source.replace("import random\n", "import random\nimport time\n", 1)
    source = source.replace(marker, marker + "    time.sleep(3.0)\n", 1)
    generator_path.write_text(source, encoding="utf-8")
    return destination


def _create_scene_and_regions(
    store: ProjectStore,
    runtime: JobRuntime,
) -> tuple[str, str]:
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
    fixture_command = store.put_bytes(
        b'{"operation":"fixture-region"}',
        media_type="application/vnd.verdflow.command+json",
    )
    region_job = runtime.submit(
        JobCommandRef(
            command_type="verdflow.fixture.region",
            command_version="1",
            artifact_id=fixture_command.artifact_id,
        ),
        idempotency_key="fixture-region",
    )
    claimed = runtime.claim_next(
        "fixture-worker",
        command_types=("verdflow.fixture.region",),
    )
    assert claimed is not None
    fixture_result = store.put_bytes(
        b'{"result_kind":"region_fixture"}',
        media_type="application/json",
    )
    runtime.succeed(
        claimed.lease,
        result_artifact_id=fixture_result.artifact_id,
    )
    geometry = store.put_bytes(
        json.dumps(
            {
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
        ).encode("utf-8"),
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


def _wait_for_launch(path: Path, timeout: float) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
        time.sleep(0.05)
    raise TimeoutError("Strategy worker did not publish launch metadata")


def _wait_for_process_death(
    inspector: SystemProcessInspector,
    host: str,
    pid: int,
    started_at_token: str,
    timeout: float,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        probe = inspector.probe(host, pid)
        if probe.status is ProcessStatus.DEAD:
            return True
        if (
            probe.status is ProcessStatus.ALIVE
            and probe.identity is not None
            and probe.identity.started_at_token != started_at_token
        ):
            return True
        time.sleep(0.05)
    return False


if __name__ == "__main__":
    raise SystemExit(main())
