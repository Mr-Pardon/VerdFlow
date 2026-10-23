"""Force-kill ProjectStore writers and verify deterministic recovery."""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
]

from verdflow.project_store import ProjectStore  # noqa: E402


WORKER = (
    ROOT
    / "tests"
    / "integration"
    / "helpers"
    / "project_store_crash_worker.py"
)
DEFAULT_OUTPUT = (
    ROOT
    / "tests"
    / "integration"
    / "results"
    / "project-store-recovery.json"
)


def wait_until_ready(
    process: subprocess.Popen[bytes],
    ready_path: Path,
    timeout_seconds: float = 15,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if ready_path.is_file():
            return
        if process.poll() is not None:
            raise RuntimeError(
                f"Crash worker exited before ready: {process.returncode}"
            )
        time.sleep(0.05)
    raise TimeoutError("Crash worker did not reach its checkpoint")


def kill_at_checkpoint(
    mode: str,
    project_path: Path,
    ready_path: Path,
) -> int:
    process = subprocess.Popen(
        [
            sys.executable,
            str(WORKER),
            mode,
            str(project_path),
            str(ready_path),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_until_ready(process, ready_path)
        process.kill()
        return process.wait(timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)


def recovery_event_count(project_path: Path) -> int:
    connection = sqlite3.connect(project_path / "project.db")
    try:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM recovery_events"
            ).fetchone()[0]
        )
    finally:
        connection.close()


def uncommitted_marker_exists(project_path: Path) -> bool:
    connection = sqlite3.connect(project_path / "project.db")
    try:
        row = connection.execute(
            """
            SELECT 1 FROM schema_metadata
            WHERE key = 'crash_probe_uncommitted'
            """
        ).fetchone()
        return row is not None
    finally:
        connection.close()


def run_probe(work_root: Path) -> dict[str, object]:
    staging_project = work_root / "staging.verdflow"
    staging_ready = work_root / "staging.ready"
    staging_exit = kill_at_checkpoint(
        "staging",
        staging_project,
        staging_ready,
    )
    with ProjectStore.open(staging_project) as recovered:
        staging_artifacts = recovered.artifact_count()
        staging_objects = recovered.object_count()
        staging_files = list(recovered.staging_path.glob("*.tmp"))
    staging_events = recovery_event_count(staging_project)

    transaction_project = work_root / "transaction.verdflow"
    with ProjectStore.create(transaction_project, "Crash transaction"):
        pass
    transaction_ready = work_root / "transaction.ready"
    transaction_exit = kill_at_checkpoint(
        "transaction",
        transaction_project,
        transaction_ready,
    )
    with ProjectStore.open(transaction_project):
        pass
    transaction_events = recovery_event_count(transaction_project)
    marker_exists = uncommitted_marker_exists(transaction_project)

    checks = {
        "staging_worker_was_killed": staging_exit != 0,
        "committed_artifact_survived": staging_artifacts == 1,
        "committed_object_survived": staging_objects == 1,
        "partial_staging_was_removed": not staging_files,
        "staging_recovery_was_audited": staging_events == 1,
        "transaction_worker_was_killed": transaction_exit != 0,
        "uncommitted_transaction_rolled_back": not marker_exists,
        "transaction_recovery_was_audited": transaction_events == 1,
    }
    return {
        "schema": "verdflow.project-store-recovery-probe",
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
            prefix="verdflow-recovery-",
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
