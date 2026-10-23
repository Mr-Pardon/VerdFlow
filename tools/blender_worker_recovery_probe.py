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
    str(ROOT / "packages" / "project-store" / "src"),
    str(ROOT / "packages" / "core" / "src"),
    str(ROOT / "adapters" / "blender-worker" / "src"),
]

from verdflow.blender_worker import (  # noqa: E402
    BLENDER_PROBE_COMMAND,
    BlenderWorkerConfig,
    BlenderWorkerSupervisor,
    locate_blender,
)
from verdflow.contracts import (  # noqa: E402
    JobCommandRef,
    JobStatus,
    WorkerProcessStatus,
)
from verdflow.core import JobRuntime  # noqa: E402
from verdflow.project_store import (  # noqa: E402
    ProcessStatus,
    ProjectStore,
    SystemProcessInspector,
)


WORK_ROOT = ROOT / "tests" / ".tmp" / "blender-worker-recovery"
RESULT_PATH = (
    ROOT
    / "tests"
    / "integration"
    / "results"
    / "blender-worker-recovery.json"
)
CRASH_HOST = (
    ROOT
    / "tests"
    / "integration"
    / "helpers"
    / "blender_worker_crash_host.py"
)


def main() -> int:
    blender = locate_blender()
    if blender is None:
        raise SystemExit("Blender executable was not found")
    if WORK_ROOT.exists():
        shutil.rmtree(WORK_ROOT)
    WORK_ROOT.mkdir(parents=True)
    project_path = WORK_ROOT / "recovery.verdflow"
    runtime_path = WORK_ROOT / "runtime"

    with ProjectStore.create(project_path, "Blender Recovery") as store:
        command = store.put_bytes(
            json.dumps(
                {
                    "schema": "verdflow.blender-worker.command",
                    "version": "1",
                    "operation": "probe",
                    "duration_seconds": 3.0,
                }
            ).encode("utf-8"),
            media_type="application/vnd.verdflow.command+json",
            schema_id="verdflow.blender-worker.command",
            schema_version="1",
        )
        runtime = JobRuntime(store, lease_duration=timedelta(seconds=1))
        job = runtime.submit(
            JobCommandRef(
                command_type=BLENDER_PROBE_COMMAND,
                command_version="1",
                artifact_id=command.artifact_id,
            ),
            idempotency_key="blender-crash-recovery",
            max_attempts=2,
        )

    host = subprocess.Popen(
        [
            sys.executable,
            str(CRASH_HOST),
            str(project_path),
            str(runtime_path),
            str(blender),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    launch_path = wait_for_launch(runtime_path, timeout=30.0)
    launch = json.loads(launch_path.read_text(encoding="utf-8"))
    child_pid = int(launch["pid"])
    child_token = str(launch["started_at_token"])
    containment_mode = str(launch["containment_mode"])
    host.kill()
    host.wait(timeout=5.0)

    inspector = SystemProcessInspector()
    child_reaped = wait_for_process_death(
        inspector,
        str(launch["host"]),
        child_pid,
        child_token,
        timeout=5.0,
    )
    time.sleep(1.3)

    with ProjectStore.open(project_path) as store:
        runtime = JobRuntime(store, lease_duration=timedelta(seconds=1))
        supervisor = BlenderWorkerSupervisor(
            runtime,
            BlenderWorkerConfig(
                blender_executable=blender,
                runtime_root=runtime_path,
                ready_timeout_seconds=30.0,
                heartbeat_timeout_seconds=5.0,
                execution_timeout_seconds=30.0,
            ),
            worker_id="recovery-worker",
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
            "windows_job_object_used": (
                containment_mode == "windows-job-object"
            ),
            "core_process_killed": host.returncode is not None,
            "blender_child_reaped": child_reaped,
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
            "stale_control_cleaned": not any(runtime_path.iterdir()),
        }
        report = {
            "schema": "verdflow.blender-worker-recovery-probe",
            "version": "1",
            "passed": all(checks.values()),
            "checks": checks,
            "containment_mode": containment_mode,
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


def wait_for_launch(runtime_path: Path, timeout: float) -> Path:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        matches = list(runtime_path.glob("*/launch.json"))
        if matches:
            return matches[0]
        time.sleep(0.05)
    raise TimeoutError("Blender worker did not publish launch metadata")


def wait_for_process_death(
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
