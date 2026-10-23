"""Run a real-file ProjectStore deduplication and portability probe."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
]

from verdflow.project_store import ProjectStore  # noqa: E402


CHUNK_SIZE = 8 * 1024 * 1024


def sha256_stream(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    while chunk := stream.read(CHUNK_SIZE):
        digest.update(chunk)
    return digest.hexdigest()


def directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def python_tree_sha256(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    files = sorted(
        (
            path
            for root in paths
            for path in root.rglob("*.py")
            if "__pycache__" not in path.parts
        ),
        key=lambda item: item.as_posix().lower(),
    )
    for path in files:
        relative = path.relative_to(ROOT).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def run_probe(source: Path, project_path: Path) -> dict[str, object]:
    source = source.resolve()
    project_path = project_path.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if project_path.exists():
        raise FileExistsError(project_path)

    started = time.perf_counter()
    with ProjectStore.create(project_path, "ProjectStore Probe") as store:
        first_started = time.perf_counter()
        first = store.put_file(source, media_type="application/octet-stream")
        first_seconds = time.perf_counter() - first_started

        repeated_started = time.perf_counter()
        repeated = store.put_file(source, media_type="application/octet-stream")
        repeated_seconds = time.perf_counter() - repeated_started

        counts_before_close = {
            "objects": store.object_count(),
            "artifacts": store.artifact_count(),
        }

    wal_after_close = (project_path / "project.db-wal").exists()
    lock_after_close = (project_path / "project.lock").exists()
    with ProjectStore.open(project_path) as reopened:
        with reopened.open_artifact(first.artifact_id) as stream:
            reopened_hash = sha256_stream(stream)
        reopened_counts = {
            "objects": reopened.object_count(),
            "artifacts": reopened.artifact_count(),
        }

    return {
        "schema": "verdflow.project-store-probe.v1",
        "created_at": datetime.now(UTC).isoformat(),
        "producer": {
            "python": sys.version.split()[0],
            "sqlite": sqlite3.sqlite_version,
            "implementation_sha256": python_tree_sha256(
                [
                    ROOT / "packages" / "contracts" / "src",
                    ROOT / "packages" / "project-store" / "src",
                ]
            ),
        },
        "source": {
            "filename": source.name,
            "byte_size": source.stat().st_size,
            "sha256": first.content_hash,
        },
        "project": {
            "directory_name": project_path.name,
            "byte_size_after_close": directory_size(project_path),
            "wal_exists_after_close": wal_after_close,
            "lock_exists_after_close": lock_after_close,
        },
        "deduplication": {
            "same_artifact_id": first.artifact_id == repeated.artifact_id,
            "counts_before_close": counts_before_close,
            "counts_after_reopen": reopened_counts,
        },
        "integrity": {
            "reopened_hash": reopened_hash,
            "reopened_hash_matches": reopened_hash == first.content_hash,
        },
        "timing_seconds": {
            "first_import": round(first_seconds, 6),
            "repeated_import": round(repeated_seconds, 6),
            "total": round(time.perf_counter() - started, 6),
        },
    }


def write_report(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = run_probe(args.source, args.project)
    write_report(args.output.resolve(), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
