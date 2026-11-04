"""Isolated subprocess entry point for geospatial document processing."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import uuid
import traceback
import zipfile


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from verdflow.geospatial_worker.processing import (  # noqa: E402
    prepare_regions,
    sample_elevation,
)
from verdflow.geospatial_worker.errors import GeospatialDataError  # noqa: E402


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
        traceback.print_exc()
        if isinstance(error, GeospatialDataError):
            code, message = error.code, str(error)
        elif isinstance(error, MemoryError):
            code, message = "geo_memory_exhausted", "处理地理数据时内存不足，任务已停止。请减少面数、简化轮廓或分批建立项目；现有项目数据已保留。"
        elif isinstance(error, json.JSONDecodeError):
            code, message = "geo_invalid_json", f"JSON 数据在第 {error.lineno} 行、第 {error.colno} 列存在语法错误，请检查 GeoJSON 文件是否完整。"
        elif isinstance(error, (UnicodeError, LookupError)):
            code, message = "geo_encoding_invalid", "区域属性表的字符编码无法解析，请检查或补齐正确的 .cpg 文件后重新导入。"
        elif isinstance(error, (OSError, zipfile.BadZipFile)):
            code, message = "geo_file_unreadable", "无法读取地理数据，请检查文件完整性、访问权限及 SHP 配套文件；项目已保留。"
        else:
            code, message = "geo_processing_failed", "地理数据处理未能完成，任务已停止且项目已保留。请检查数据格式、有效几何及 SHP＋TIF 的坐标对齐；详细原因可在任务诊断中查看。"
        _write_json(
            args.result,
            {
                "schema": "verdflow.geospatial-worker.result",
                "version": "1",
                "status": "failed",
                "error_code": code,
                "message": message,
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
