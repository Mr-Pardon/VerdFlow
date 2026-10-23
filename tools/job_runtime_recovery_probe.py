"""Kill a Core process and verify persistent Job recovery and fencing."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
    str(ROOT / "packages" / "core" / "src"),
]

from verdflow.contracts import JobEventType, JobLease, JobStatus  # noqa: E402
from verdflow.core import JobRuntime  # noqa: E402
from verdflow.project_store import (  # noqa: E402
    JobLeaseConflictError,
    ProjectStore,
)


WORKER = (
    ROOT
    / "tests"
    / "integration"
    / "helpers"
    / "job_runtime_crash_worker.py"
)
DEFAULT_OUTPUT = (
    ROOT
    / "tests"
    / "integration"
    / "results"
    / "job-runtime-recovery.json"
)


def wait_for_ready(
    process: subprocess.Popen[bytes],
    ready_path: Path,
    timeout_seconds: float = 15,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if ready_path.is_file():
            return json.loads(ready_path.read_text(encoding="utf-8"))
        if process.poll() is not None:
            raise RuntimeError(
                f"Crash worker exited before ready: {process.returncode}"
            )
        time.sleep(0.05)
    raise TimeoutError("Job crash worker did not reach its checkpoint")


def run_probe(work_root: Path) -> dict[str, object]:
    project_path = work_root / "job-runtime.verdflow"
    ready_path = work_root / "job.ready.json"
    process = subprocess.Popen(
        [
            sys.executable,
            str(WORKER),
            str(project_path),
            str(ready_path),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        ready = wait_for_ready(process, ready_path)
        process.kill()
        exit_code = process.wait(timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)

    expires_at = datetime.fromisoformat(str(ready["expires_at"]))
    remaining = (expires_at - datetime.now(UTC)).total_seconds()
    if remaining >= 0:
        time.sleep(remaining + 0.1)

    old_lease = JobLease(
        job_id=str(ready["job_id"]),
        lease_token=str(ready["lease_token"]),
        worker_id=str(ready["worker_id"]),
        attempt_count=int(ready["attempt_count"]),
        expires_at=str(ready["expires_at"]),
    )
    with ProjectStore.open(project_path) as store:
        runtime = JobRuntime(
            store,
            lease_duration=timedelta(milliseconds=400),
        )
        late_result_rejected = False
        try:
            runtime.succeed(old_lease)
        except JobLeaseConflictError:
            late_result_rejected = True

        recovered = runtime.recover_expired()
        queued_after_recovery = (
            runtime.get(old_lease.job_id).status == JobStatus.QUEUED
        )
        second = runtime.claim_next("probe-worker-after-crash")
        if second is None:
            raise RuntimeError("Recovered job was not claimable")
        result = store.put_bytes(
            b"probe-result",
            media_type="application/octet-stream",
        )
        completed = runtime.succeed(
            second.lease,
            result_artifact_id=result.artifact_id,
        )
        replay = runtime.events(old_lease.job_id, after_sequence=2)
        all_events = runtime.events(old_lease.job_id)
        attempts = runtime.attempts(old_lease.job_id)

    expected_event_types = [
        JobEventType.SUBMITTED,
        JobEventType.STARTED,
        JobEventType.INTERRUPTED,
        JobEventType.RETRY_SCHEDULED,
        JobEventType.STARTED,
        JobEventType.SUCCEEDED,
    ]
    checks = {
        "core_process_was_killed": exit_code != 0,
        "expired_late_result_was_rejected": late_result_rejected,
        "expired_attempt_was_interrupted": recovered.interrupted_count == 1,
        "job_was_requeued": recovered.requeued_count == 1,
        "queued_state_survived_core_restart": queued_after_recovery,
        "new_attempt_was_claimed": second.lease.attempt_count == 2,
        "new_attempt_completed": completed.status == JobStatus.SUCCEEDED,
        "attempt_history_was_preserved": (
            [attempt.worker_id for attempt in attempts]
            == [
                "probe-worker-before-crash",
                "probe-worker-after-crash",
            ]
            and [attempt.status for attempt in attempts]
            == [JobStatus.INTERRUPTED, JobStatus.SUCCEEDED]
        ),
        "event_history_is_monotonic": (
            [event.sequence for event in all_events]
            == list(range(1, len(all_events) + 1))
        ),
        "event_history_matches_recovery": (
            [event.event_type for event in all_events]
            == expected_event_types
        ),
        "reconnect_replay_skips_seen_events": (
            [event.sequence for event in replay] == [3, 4, 5, 6]
        ),
    }
    return {
        "schema": "verdflow.job-runtime-recovery-probe",
        "version": 1,
        "executed_at": datetime.now(UTC).isoformat(),
        "python": sys.version,
        "checks": checks,
        "passed": all(checks.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--keep-workdir", type=Path)
    arguments = parser.parse_args()

    if arguments.keep_workdir is None:
        temporary = tempfile.mkdtemp(
            prefix="verdflow-job-runtime-",
            dir=ROOT / "tests" / ".tmp",
        )
        work_root = Path(temporary)
        remove_work_root = True
    else:
        work_root = arguments.keep_workdir.resolve()
        work_root.mkdir(parents=True, exist_ok=True)
        remove_work_root = False

    try:
        result = run_probe(work_root)
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(
            json.dumps(result, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        print(json.dumps(result["checks"], indent=2))
        return 0 if result["passed"] else 1
    finally:
        if remove_work_root:
            shutil.rmtree(work_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
