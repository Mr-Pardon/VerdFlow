from __future__ import annotations

import json
import shutil
import sys
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
    JobEventType,
    JobStatus,
    WorkerProcessStatus,
)
from verdflow.core import JobRuntime  # noqa: E402
from verdflow.project_store import ProjectStore  # noqa: E402


WORK_ROOT = ROOT / "tests" / ".tmp" / "blender-worker-probe"
RESULT_PATH = (
    ROOT / "tests" / "integration" / "results" / "blender-worker-probe.json"
)


def main() -> int:
    blender = locate_blender()
    if blender is None:
        raise SystemExit("Blender executable was not found")
    if WORK_ROOT.exists():
        shutil.rmtree(WORK_ROOT)
    WORK_ROOT.mkdir(parents=True)
    project_path = WORK_ROOT / "probe.verdflow"
    runtime_path = WORK_ROOT / "runtime"

    with ProjectStore.create(project_path, "Blender Worker Probe") as store:
        command = store.put_bytes(
            json.dumps(
                {
                    "schema": "verdflow.blender-worker.command",
                    "version": "1",
                    "operation": "probe",
                    "duration_seconds": 1.0,
                },
                sort_keys=True,
            ).encode("utf-8"),
            media_type="application/vnd.verdflow.command+json",
            schema_id="verdflow.blender-worker.command",
            schema_version="1",
        )
        runtime = JobRuntime(
            store,
            lease_duration=timedelta(seconds=10),
        )
        job = runtime.submit(
            JobCommandRef(
                command_type=BLENDER_PROBE_COMMAND,
                command_version="1",
                artifact_id=command.artifact_id,
            ),
            idempotency_key="real-blender-probe",
        )
        supervisor = BlenderWorkerSupervisor(
            runtime,
            BlenderWorkerConfig(
                blender_executable=blender,
                runtime_root=runtime_path,
                ready_timeout_seconds=30.0,
                heartbeat_timeout_seconds=5.0,
                execution_timeout_seconds=30.0,
            ),
        )
        completed = supervisor.run_once()
        assert completed is not None
        process = store.get_worker_process(job.job_id, 1)
        events = runtime.events(job.job_id)
        result: dict[str, object] = {}
        if completed.result_artifact_id is not None:
            with store.open_artifact(completed.result_artifact_id) as stream:
                result = json.load(stream)

        coordinate = result.get("coordinate_frame", {})
        blender_result = result.get("blender", {})
        checks = {
            "job_succeeded": completed.status is JobStatus.SUCCEEDED,
            "process_exited": (
                process.status is WorkerProcessStatus.EXITED
                and process.exit_code == 0
            ),
            "blender_version_reported": bool(
                isinstance(blender_result, dict)
                and blender_result.get("version")
            ),
            "canonical_frame_reported": (
                isinstance(coordinate, dict)
                and coordinate.get("handedness") == "right-handed"
                and coordinate.get("up_axis") == "+Z"
                and coordinate.get("linear_unit") == "meter"
            ),
            "offset_not_applied": (
                isinstance(coordinate, dict)
                and coordinate.get("offset_applied") is False
            ),
            "progress_persisted": any(
                event.event_type is JobEventType.PROGRESS
                for event in events
            ),
            "runtime_control_cleaned": not any(runtime_path.iterdir()),
        }
        report = {
            "schema": "verdflow.blender-worker-probe",
            "version": "1",
            "passed": all(checks.values()),
            "blender_executable": str(blender),
            "checks": checks,
            "job": {
                "job_id": job.job_id,
                "status": completed.status.value,
                "event_count": len(events),
                "result_artifact_id": completed.result_artifact_id,
            },
            "process": {
                "pid": process.pid,
                "status": process.status.value,
                "exit_code": process.exit_code,
            },
            "result": result,
        }
        RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESULT_PATH.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
