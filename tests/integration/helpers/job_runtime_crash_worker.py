"""Core crash target used by the Job Runtime recovery probe."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
    str(ROOT / "packages" / "core" / "src"),
]

from verdflow.contracts import JobCommandRef  # noqa: E402
from verdflow.core import JobRuntime  # noqa: E402
from verdflow.project_store import ProjectStore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project_path", type=Path)
    parser.add_argument("ready_path", type=Path)
    arguments = parser.parse_args()

    store = ProjectStore.create(arguments.project_path, "Job crash probe")
    command_artifact = store.put_bytes(
        b'{"operation":"crash-probe"}',
        media_type="application/vnd.verdflow.command+json",
        schema_id="verdflow.crash-probe",
        schema_version="1",
    )
    runtime = JobRuntime(
        store,
        lease_duration=timedelta(milliseconds=400),
    )
    job = runtime.submit(
        JobCommandRef(
            command_type="probe.wait",
            command_version="1",
            artifact_id=command_artifact.artifact_id,
        ),
        idempotency_key="job-runtime-crash-probe",
        max_attempts=2,
    )
    claimed = runtime.claim_next("probe-worker-before-crash")
    if claimed is None:
        raise RuntimeError("Probe job was not claimable")

    arguments.ready_path.write_text(
        json.dumps(
            {
                "job_id": job.job_id,
                "lease_token": claimed.lease.lease_token,
                "worker_id": claimed.lease.worker_id,
                "attempt_count": claimed.lease.attempt_count,
                "expires_at": claimed.lease.expires_at,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    while True:
        time.sleep(60)


if __name__ == "__main__":
    raise SystemExit(main())
