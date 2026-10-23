"""Isolated subprocess entry point for geospatial document processing."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import uuid


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from verdflow.geospatial_worker.processing import (  # noqa: E402
    prepare_regions,
    sample_elevation,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--command", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    try:
        command = json.loads(args.command.read_text(encoding="utf-8"))
        if not isinstance(command, dict):
            raise ValueError("Worker command must be an object")
        args.output_dir.mkdir(parents=True, exist_ok=True)
        operation = command.get("operation")
        output_files: list[dict[str, str]] = []
        if operation == "prepare_regions":
            documents = prepare_regions(command, args.output_dir)
            specs = {
                "source_manifest": (
                    "source-manifest.json",
                    "application/vnd.verdflow.region-source-manifest+json",
                    "verdflow.region-source-manifest",
                ),
                "regions": (
                    "regions.json",
                    "application/vnd.verdflow.regions+json",
                    "verdflow.regions",
                ),
                "geometry_metrics": (
                    "region-metrics.json",
                    "application/vnd.verdflow.region-metrics+json",
                    "verdflow.region-metrics",
                ),
            }
            for role, (filename, media_type, schema_id) in specs.items():
                _write_json(args.output_dir / filename, documents[role])
                output_files.append(
                    {
                        "role": role,
                        "relative_path": filename,
                        "media_type": media_type,
                        "schema_id": schema_id,
                        "schema_version": "1",
                    }
                )
        elif operation == "sample_elevation":
            _write_json(args.output_dir / "points.json", sample_elevation(command))
            output_files.append(
                {
                    "role": "points",
                    "relative_path": "points.json",
                    "media_type": "application/vnd.verdflow.points+json",
                    "schema_id": "verdflow.points",
                    "schema_version": "2",
                }
            )
        else:
            raise ValueError(f"Unsupported operation: {operation}")
        _write_json(
            args.result,
            {
                "schema": "verdflow.geospatial-worker.result",
                "version": "1",
                "status": "succeeded",
                "operation": operation,
                "source_id": command.get("source_id"),
                "output_files": output_files,
            },
        )
        return 0
    except Exception as error:
        _write_json(
            args.result,
            {
                "schema": "verdflow.geospatial-worker.result",
                "version": "1",
                "status": "failed",
                "message": str(error),
            },
        )
        return 1


def _write_json(path: Path, payload: object) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False),
        encoding="utf-8",
    )
    os.replace(temporary, path)


if __name__ == "__main__":
    raise SystemExit(main())
