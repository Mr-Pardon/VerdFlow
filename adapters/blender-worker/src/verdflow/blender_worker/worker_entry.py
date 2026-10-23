"""Standalone script executed inside Blender. Do not import VerdFlow packages."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import bpy


PROTOCOL_VERSION = "1"
PACKAGE_SOURCE = Path(__file__).resolve().parents[2]
if str(PACKAGE_SOURCE) not in sys.path:
    sys.path.insert(0, str(PACKAGE_SOURCE))

from verdflow.blender_worker.capabilities import (  # noqa: E402
    capability_manifest,
    extract_regions,
    inspect_asset_library,
    inspect_scene,
    save_scene_cache,
    sample_elevation,
)


class Cancelled(RuntimeError):
    pass


def atomic_json(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    deadline = time.monotonic() + 2.0
    while True:
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                temporary.unlink(missing_ok=True)
                raise
            time.sleep(0.01)


def arguments() -> tuple[Path, Path]:
    tail = sys.argv[sys.argv.index("--") + 1 :]
    values = dict(zip(tail[::2], tail[1::2]))
    return Path(values["--control-dir"]), Path(values["--command"])


def blender_build_hash() -> str:
    value = bpy.app.build_hash
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)


def main() -> None:
    control_dir, command_path = arguments()
    command = json.loads(command_path.read_text(encoding="utf-8"))
    progress_dir = control_dir / "progress-events"
    progress_dir.mkdir(exist_ok=True)
    state: dict[str, object] = {
        "progress": 0.0,
        "stage": "starting",
        "sequence": 0,
        "changed": threading.Event(),
        "progress_sequence": 0,
        "progress_dir": progress_dir,
    }
    state_lock = threading.Lock()
    stop = threading.Event()
    cancel_path = control_dir / "cancel.request"

    atomic_json(
        control_dir / "ready.json",
        {
            "schema": "verdflow.blender-worker.ready",
            "version": PROTOCOL_VERSION,
            "pid": os.getpid(),
            "blender_version": bpy.app.version_string,
            "capability_bundle": capability_manifest(),
        },
    )

    def heartbeat_loop() -> None:
        changed = state["changed"]
        while not stop.is_set():
            changed.wait(0.25)
            changed.clear()
            if stop.is_set():
                break
            with state_lock:
                state["sequence"] = int(state["sequence"]) + 1
                payload = {
                    "schema": "verdflow.blender-worker.heartbeat",
                    "version": PROTOCOL_VERSION,
                    "pid": os.getpid(),
                    "sequence": state["sequence"],
                    "progress": state["progress"],
                    "stage": state["stage"],
                    "monotonic_seconds": time.monotonic(),
                }
            atomic_json(control_dir / "heartbeat.json", payload)

    heartbeat_thread = threading.Thread(
        target=heartbeat_loop,
        name="verdflow-worker-heartbeat",
        daemon=True,
    )
    heartbeat_thread.start()

    try:
        operation = str(command.get("operation", ""))
        result = run_operation(
            operation,
            command,
            control_dir,
            cancel_path,
            state,
            state_lock,
        )
        with state_lock:
            state["stage"] = "completed"
            state["progress"] = 1.0
        atomic_json(control_dir / "result.json", result)
    except Cancelled as error:
        atomic_json(
            control_dir / "result.json",
            {
                "schema": "verdflow.blender-worker.result",
                "version": PROTOCOL_VERSION,
                "status": "cancelled",
                "message": str(error),
            },
        )
    except Exception as error:
        atomic_json(
            control_dir / "result.json",
            {
                "schema": "verdflow.blender-worker.result",
                "version": PROTOCOL_VERSION,
                "status": "failed",
                "error_type": type(error).__name__,
                "message": str(error),
            },
        )
        raise
    finally:
        stop.set()
        heartbeat_thread.join(timeout=1.0)


def run_operation(
    operation: str,
    command: dict[str, object],
    control_dir: Path,
    cancel_path: Path,
    state: dict[str, object],
    state_lock: threading.Lock,
) -> dict[str, object]:
    if operation == "probe":
        duration = max(0.1, float(command.get("duration_seconds", 1.0)))
        started = time.monotonic()
        while True:
            require_not_cancelled(cancel_path)
            fraction = min(1.0, (time.monotonic() - started) / duration)
            set_progress(
                state,
                state_lock,
                "probing_blender",
                0.05 + fraction * 0.9,
            )
            if fraction >= 1.0:
                break
            time.sleep(min(0.05, duration / 10.0))
        scene = bpy.context.scene
        return base_result(
            operation,
            scene={
                "object_count": len(scene.objects),
                "mesh_object_count": sum(
                    1 for item in scene.objects if item.type == "MESH"
                ),
                "unit_system": scene.unit_settings.system,
                "scale_length": scene.unit_settings.scale_length,
                "length_unit": scene.unit_settings.length_unit,
            },
            coordinate_frame={
                "handedness": "right-handed",
                "up_axis": "+Z",
                "linear_unit": "meter",
                "crs_kind": "unknown",
                "local_origin": [0.0, 0.0, 0.0],
                "offset_applied": False,
            },
        )

    if operation == "inspect_asset_library":
        require_not_cancelled(cancel_path)
        set_progress(state, state_lock, "opening_asset_library", 0.1)
        inspection = inspect_asset_library(
            Path(str(command["library_path"])),
            library_id=str(command["library_id"]),
            required_collection_names=list(
                command.get("required_collection_names", [])
            ),
        )
        set_progress(state, state_lock, "writing_asset_facts", 0.85)
        outputs = control_dir / "outputs"
        outputs.mkdir(exist_ok=True)
        inspection_path = outputs / "asset_library_inspection.json"
        atomic_json(inspection_path, inspection)
        return base_result(
            operation,
            library_id=command.get("library_id"),
            output_files=[
                output_file(
                    "asset_library_inspection",
                    inspection_path,
                    control_dir,
                    (
                        "application/vnd.verdflow."
                        "blender-asset-library-inspection+json"
                    ),
                    "verdflow.blender-asset-library-inspection",
                )
            ],
        )

    source_members_value = command.get("source_members")
    source_members = (
        list(source_members_value)
        if isinstance(source_members_value, list)
        else None
    )
    source_path = (
        None
        if source_members is not None
        else Path(str(command["source_path"]))
    )
    source_name = str(
        command.get(
            "source_name",
            source_path.name if source_path is not None else "source-assembly",
        )
    )
    outputs = control_dir / "outputs"
    outputs.mkdir(exist_ok=True)
    require_not_cancelled(cancel_path)
    if operation == "inspect_scene":
        def report_inspection_stage(stage: str, progress: float) -> None:
            set_progress(state, state_lock, stage, progress)

        manifest, fact_report = inspect_scene(
            source_path,
            source_name=source_name,
            source_members=source_members,
            import_settings=dict(command.get("import_settings", {})),
            stage_callback=report_inspection_stage,
            cancel_check=lambda: require_not_cancelled(cancel_path),
        )
        set_progress(state, state_lock, "saving_scene_cache", 0.78)
        scene_cache_path = outputs / "scene-cache.blend"
        save_scene_cache(scene_cache_path)
        set_progress(state, state_lock, "writing_scene_facts", 0.9)
        manifest_path = outputs / "scene_manifest.json"
        fact_path = outputs / "fact_report.json"
        atomic_json(manifest_path, manifest)
        atomic_json(fact_path, fact_report)
        return base_result(
            operation,
            source_id=command.get("source_id"),
            scene_cache_file=scene_cache_path.relative_to(control_dir).as_posix(),
            output_files=[
                output_file(
                    "scene_manifest",
                    manifest_path,
                    control_dir,
                    (
                        "application/vnd.verdflow."
                        "scene-inspection-manifest+json"
                    ),
                    "verdflow.scene-inspection-manifest",
                ),
                output_file(
                    "material_fact_report",
                    fact_path,
                    control_dir,
                    "application/vnd.verdflow.material-facts+json",
                    "fbx-region-tagger.fact-report",
                ),
            ],
        )

    if operation == "extract_regions":
        rule_pack_path = Path(str(command["rule_pack_path"]))
        rule_pack = json.loads(rule_pack_path.read_text(encoding="utf-8"))
        def report_region_stage(stage: str, progress: float) -> None:
            set_progress(state, state_lock, stage, progress)

        regions, metrics = extract_regions(
            source_path,
            rule_pack,
            source_name=source_name,
            source_members=source_members,
            collection_name=str(
                command.get(
                    "collection_name",
                    "VerdFlow Extracted Regions",
                )
            ),
            allow_contains=bool(command.get("allow_contains", False)),
            import_settings=dict(command.get("import_settings", {})),
            scene_cache_path=(
                Path(str(command["scene_cache_path"]))
                if command.get("scene_cache_path")
                else None
            ),
            stage_callback=report_region_stage,
            cancel_check=lambda: require_not_cancelled(cancel_path),
        )
        set_progress(state, state_lock, "writing_regions", 0.85)
        regions_path = outputs / "regions.json"
        metrics_path = outputs / "geometry_metrics.json"
        atomic_json(regions_path, regions)
        atomic_json(metrics_path, metrics)
        return base_result(
            operation,
            source_id=command.get("source_id"),
            output_files=[
                output_file(
                    "regions",
                    regions_path,
                    control_dir,
                    "application/vnd.verdflow.regions+json",
                    "verdflow.regions",
                ),
                output_file(
                    "geometry_metrics",
                    metrics_path,
                    control_dir,
                    "application/vnd.verdflow.geometry-metrics+json",
                    "verdflow.region-metrics",
                ),
            ],
        )

    if operation == "sample_elevation":
        points_path = Path(str(command["points_path"]))
        set_progress(state, state_lock, "importing_fbx", 0.1)

        def report_progress(completed: int, total: int) -> None:
            fraction = completed / total if total else 1.0
            set_progress(
                state,
                state_lock,
                "sampling_elevation",
                0.3 + fraction * 0.6,
            )

        def report_stage(stage: str, progress: float) -> None:
            set_progress(state, state_lock, stage, progress)

        sampled = sample_elevation(
            source_path,
            points_path,
            source_members=source_members,
            import_settings=dict(command.get("import_settings", {})),
            target_object_names=list(
                command.get("target_object_names", [])
            ),
            region_source_member_ids=dict(
                command.get("region_source_member_ids", {})
            ),
            direction=str(command.get("direction", "Z_DOWN")),
            ray_start_offset=float(
                command.get("ray_start_offset", 500.0)
            ),
            ray_distance=float(command.get("ray_distance", 1500.0)),
            include_hidden=bool(command.get("include_hidden", False)),
            progress_callback=report_progress,
            stage_callback=report_stage,
            cancel_check=lambda: require_not_cancelled(cancel_path),
            scene_cache_path=(
                Path(str(command["scene_cache_path"]))
                if command.get("scene_cache_path")
                else None
            ),
        )
        set_progress(state, state_lock, "writing_sampled_points", 0.92)
        points_output_path = outputs / "points.json"
        atomic_json(points_output_path, sampled.point_set)
        return base_result(
            operation,
            source_id=command.get("source_id"),
            parent_point_set_id=command.get("parent_point_set_id"),
            metrics={"elevation": sampled.metrics},
            output_files=[
                output_file(
                    "points",
                    points_output_path,
                    control_dir,
                    "application/vnd.verdflow.points+json",
                    "verdflow.points",
                    "2",
                )
            ],
        )
    raise ValueError(f"Unsupported Blender worker operation: {operation}")


def set_progress(
    state: dict[str, object],
    lock: threading.Lock,
    stage: str,
    progress: float,
) -> None:
    with lock:
        state["stage"] = stage
        state["progress"] = progress
        state["progress_sequence"] = int(state["progress_sequence"]) + 1
        sequence = int(state["progress_sequence"])
        progress_dir = state.get("progress_dir")
        changed = state.get("changed")
    if isinstance(progress_dir, Path):
        atomic_json(
            progress_dir / f"{sequence:08d}.json",
            {
                "schema": "verdflow.blender-worker.progress",
                "version": PROTOCOL_VERSION,
                "sequence": sequence,
                "stage": stage,
                "progress": progress,
            },
        )
    if isinstance(changed, threading.Event):
        changed.set()


def require_not_cancelled(cancel_path: Path) -> None:
    if cancel_path.exists():
        raise Cancelled("Cancellation requested")


def output_file(
    role: str,
    path: Path,
    control_dir: Path,
    media_type: str,
    schema_id: str,
    schema_version: str = "1",
) -> dict[str, object]:
    return {
        "role": role,
        "relative_path": path.relative_to(control_dir).as_posix(),
        "media_type": media_type,
        "schema_id": schema_id,
        "schema_version": schema_version,
    }


def base_result(
    operation: str,
    **values: object,
) -> dict[str, object]:
    return {
        "schema": "verdflow.blender-worker.result",
        "version": PROTOCOL_VERSION,
        "status": "succeeded",
        "operation": operation,
        "worker_pid": os.getpid(),
        "blender": {
            "version": bpy.app.version_string,
            "version_tuple": list(bpy.app.version),
            "build_hash": blender_build_hash(),
        },
        "capability_bundle": capability_manifest(),
        **values,
    }


if __name__ == "__main__":
    main()
