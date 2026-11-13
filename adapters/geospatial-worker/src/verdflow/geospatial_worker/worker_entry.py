"""Isolated subprocess entry point for geospatial document processing."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import errno
import json
import os
from pathlib import Path
import sqlite3
import sys
from time import monotonic
import uuid
import traceback
import zipfile


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from verdflow.geospatial_worker.processing import (  # noqa: E402
    write_prepared_regions,
    write_sampled_elevation,
)
from verdflow.geospatial_worker.errors import GeospatialDataError, is_host_memory_error  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--command", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--progress", type=Path)
    parser.add_argument("--domain-root", action="append", default=[])
    args = parser.parse_args()
    sys.path[:0] = args.domain_root
    operation = None
    last_phase, last_report = None, float("-inf")

    def progress(phase: str, completed: int, total: int) -> None:
        nonlocal last_phase, last_report
        now = monotonic()
        if args.progress is not None and (phase != last_phase or now - last_report >= 0.5):
            try:
                args.progress.write_text(json.dumps([phase, completed, total]), encoding="ascii")
            except PermissionError:
                return  # Windows may briefly hold the file open in the supervisor.
            last_phase, last_report = phase, now

    try:
        command = json.loads(args.command.read_text(encoding="utf-8"))
        if not isinstance(command, dict):
            raise ValueError("Worker command must be an object")
        args.output_dir.mkdir(parents=True, exist_ok=True)
        operation = command.get("operation")
        output_files: list[dict[str, str]] = []
        if operation in {"prepare_regions", "prepare_imagery_regions"}:
            if operation == "prepare_imagery_regions":
                from verdflow.geospatial_worker.imagery import prepare_imagery_regions

                documents = prepare_imagery_regions(command, args.output_dir, progress, write_candidate=True)
                manifest = documents["source_manifest"]
                manifest["review"] = documents["review"]
                output_files.append({
                    "role": "vegetation_mask", "relative_path": "vegetation-mask.tif",
                    "media_type": "image/tiff", "schema_id": "verdflow.imagery-vegetation-mask",
                    "schema_version": "1",
                })
                output_files.append({
                    "role": "imagery_preview", "relative_path": "imagery-preview.png",
                    "media_type": "image/png", "schema_id": "verdflow.imagery-preview",
                    "schema_version": "1",
                })
                output_files.extend([
                    {"role": "imagery_overview", "relative_path": "imagery-overview.png",
                     "media_type": "image/png", "schema_id": "verdflow.imagery-review-overview", "schema_version": "1"},
                    {"role": "imagery_review_index", "relative_path": "imagery-review-index.json",
                     "media_type": "application/json", "schema_id": "verdflow.imagery-review-index", "schema_version": "1"},
                ])
                if manifest["review"].get("index_encoding"):
                    output_files.append({
                        "role": "imagery_packed_index", "relative_path": "imagery-review-index.bin",
                        "media_type": "application/octet-stream", "schema_id": "verdflow.imagery-ordinal-index",
                        "schema_version": "1",
                    })
                for index in range(1, manifest["review"]["page_count"] + 1):
                    role = f"imagery_page_{index}"
                    filename = f"{role}.json"
                    output_files.append({
                        "role": role, "relative_path": filename, "media_type": "application/json",
                        "schema_id": "verdflow.imagery-review-page", "schema_version": "1",
                    })
            else:
                with (
                    _staged_path(args.output_dir / "regions.json") as geometry_path,
                    _staged_path(args.output_dir / "region-metrics.json") as metrics_path,
                ):
                    with geometry_path.open("w", encoding="utf-8") as geometry, metrics_path.open("w", encoding="utf-8") as metrics:
                        manifest = write_prepared_regions(command, args.output_dir, geometry, metrics, progress)
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
            for index, (role, (filename, media_type, schema_id)) in enumerate(specs.items()):
                if operation == "prepare_regions":
                    progress("writing_result", index, len(specs))
                if role == "source_manifest":
                    _write_json(args.output_dir / filename, manifest)
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
            with _staged_path(args.output_dir / "points.json") as temporary:
                with temporary.open("wb") as stream:
                    write_sampled_elevation(command, stream, progress)
                progress("writing_result", 1, 1)
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
        elif is_host_memory_error(error):
            if operation == "prepare_imagery_regions":
                code, message = "imagery_memory_exhausted", "影像处理内存不足，未发布候选区域。请释放内存后重试，项目已保留。"
            else:
                code, message = "geo_memory_exhausted", "处理地理数据时内存不足，任务已停止。请减少面数、简化轮廓或分批建立项目；现有项目数据已保留。"
        elif isinstance(error, json.JSONDecodeError):
            code, message = "geo_invalid_json", f"JSON 数据在第 {error.lineno} 行、第 {error.colno} 列存在语法错误，请检查 GeoJSON 文件是否完整。"
        elif isinstance(error, (UnicodeError, LookupError)):
            code, message = "geo_encoding_invalid", "区域属性表的字符编码无法解析，请检查或补齐正确的 .cpg 文件后重新导入。"
        elif operation == "prepare_imagery_regions" and (
            isinstance(error, OSError) and error.errno == errno.ENOSPC
            or isinstance(error, sqlite3.Error) and getattr(error, "sqlite_errorcode", None) == sqlite3.SQLITE_FULL
        ):
            code, message = "imagery_disk_budget", "项目磁盘空间不足，影像任务已停止。请释放空间后重试。"
        elif operation == "prepare_imagery_regions":
            code, message = "imagery_processing_failed", "影像处理未能完成，未定版任何区域。请查看任务诊断。"
        elif operation == "sample_elevation" and isinstance(error, OSError) and error.errno == errno.ENOSPC:
            code, message = "geo_elevation_disk_full", "项目磁盘空间不足，高程结果未发布。请释放空间后重试；原始点位已保留。"
        elif operation == "prepare_regions" and isinstance(error, OSError) and error.errno == errno.ENOSPC:
            code, message = "geo_disk_budget", "项目磁盘空间不足，区域未发布。请释放空间后重试。"
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
    with _staged_path(path) as temporary:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, allow_nan=False)


@contextmanager
def _staged_path(path: Path):
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        yield temporary
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
