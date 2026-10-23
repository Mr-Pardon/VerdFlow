"""Crash target used by the ProjectStore recovery integration probe."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
]

from verdflow.project_store import ProjectStore  # noqa: E402


def signal_ready(path: Path) -> None:
    path.write_text("ready", encoding="utf-8")


def wait_for_kill() -> None:
    while True:
        time.sleep(60)


def run_staging(project_path: Path, ready_path: Path) -> None:
    store = ProjectStore.create(project_path, "Crash staging")
    store.put_bytes(
        b"committed-before-crash",
        media_type="application/octet-stream",
    )
    (store.staging_path / "interrupted.tmp").write_bytes(
        b"uncommitted-staging-data"
    )
    signal_ready(ready_path)
    wait_for_kill()


def run_transaction(project_path: Path, ready_path: Path) -> None:
    store = ProjectStore.open(project_path)
    connection = store._database.connection
    connection.execute("BEGIN IMMEDIATE")
    connection.execute(
        """
        INSERT INTO schema_metadata(key, value)
        VALUES('crash_probe_uncommitted', 'must_rollback')
        """
    )
    signal_ready(ready_path)
    wait_for_kill()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("staging", "transaction"))
    parser.add_argument("project_path", type=Path)
    parser.add_argument("ready_path", type=Path)
    arguments = parser.parse_args()

    if arguments.mode == "staging":
        run_staging(arguments.project_path, arguments.ready_path)
    else:
        run_transaction(arguments.project_path, arguments.ready_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
