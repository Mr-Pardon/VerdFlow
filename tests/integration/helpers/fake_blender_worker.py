from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path


def atomic_json(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(temporary, path)


def main() -> None:
    control_dir = Path(sys.argv[1])
    command_path = Path(sys.argv[2])
    command = json.loads(command_path.read_text(encoding="utf-8"))
    mode = command.get("test_mode", "success")
    duration = float(command.get("duration_seconds", 0.3))
    if mode == "no_ready":
        time.sleep(duration)
        return
    if mode == "delayed_ready":
        time.sleep(float(command.get("ready_delay_seconds", 0.5)))

    atomic_json(
        control_dir / "ready.json",
        {
            "schema": "verdflow.blender-worker.ready",
            "version": "1",
            "pid": os.getpid(),
            "capability_bundle": {
                "bundle_id": "fake-blender-capabilities",
                "bundle_version": "1",
                "bundle_hash": "fake",
                "capabilities": [
                    {"id": "assets.inspect-library", "version": "1"},
                    {"id": "scene.inspect", "version": "1"},
                    {"id": "regions.extract", "version": "1"},
                    {"id": "points.sample-elevation", "version": "2"},
                ],
            },
        },
    )
    stop = threading.Event()
    progress = [0.0]

    def heartbeat() -> None:
        sequence = 0
        while not stop.wait(0.05):
            sequence += 1
            atomic_json(
                control_dir / "heartbeat.json",
                {
                    "schema": "verdflow.blender-worker.heartbeat",
                    "version": "1",
                    "pid": os.getpid(),
                    "sequence": sequence,
                    "progress": progress[0],
                    "stage": "fake_work",
                },
            )

    thread = None
    if mode != "no_heartbeat":
        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
    started = time.monotonic()
    try:
        while time.monotonic() - started < duration:
            if (control_dir / "cancel.request").exists():
                return
            progress[0] = min(
                0.9,
                (time.monotonic() - started) / max(duration, 0.01),
            )
            time.sleep(0.02)
        if mode == "bad_result":
            atomic_json(control_dir / "result.json", {"not": "a protocol"})
            return
        result = {
            "schema": "verdflow.blender-worker.result",
            "version": "1",
            "status": "succeeded",
            "operation": command.get("operation", "probe"),
            "fake": True,
        }
        if command.get("operation") == "inspect_scene" and command.get(
            "emit_scene_cache"
        ):
            outputs = control_dir / "outputs"
            outputs.mkdir(exist_ok=True)
            cache = outputs / "scene-cache.blend"
            cache.write_bytes(b"fake blend cache")
            result["scene_cache_file"] = "outputs/scene-cache.blend"
        if command.get("operation") == "sample_elevation":
            points = json.loads(
                Path(str(command["points_path"])).read_text(encoding="utf-8")
            )
            points["kind"] = "elevation_resolved"
            points["resolution_policy"] = "sample_hit_else_generated"
            for point in points["points"]:
                position = list(point["generated_position"])
                position[2] = float(position[2]) + 1.0
                point["terrain_sample"] = {
                    "hit": True,
                    "position": position,
                    "normal": [0.0, 0.0, 1.0],
                    "target": "fake-terrain",
                }
                point["resolved_position"] = position
            outputs = control_dir / "outputs"
            outputs.mkdir()
            sampled_path = outputs / "points.json"
            atomic_json(sampled_path, points)
            result["output_files"] = [
                {
                    "role": "points",
                    "relative_path": "outputs/points.json",
                    "media_type": "application/vnd.verdflow.points+json",
                    "schema_id": "verdflow.points",
                    "schema_version": "2",
                }
            ]
        if command.get("emit_output"):
            outputs = control_dir / "outputs"
            outputs.mkdir(exist_ok=True)
            facts = outputs / "facts.json"
            source_members = command.get("source_members")
            member_paths = [
                Path(str(member["source_path"]))
                for member in source_members or []
            ]
            atomic_json(
                facts,
                {
                    "library_was_resolved": (
                        Path(str(command["library_path"])).is_file()
                        if command.get("operation")
                        == "inspect_asset_library"
                        else False
                    ),
                    "library_id": command.get("library_id"),
                    "source_was_staged": (
                        all(path.is_file() for path in member_paths)
                        if source_members is not None
                        else (
                            Path(str(command["source_path"])).is_file()
                            if "source_path" in command
                            else False
                        )
                    ),
                    "source_member_count": len(member_paths),
                    "source_members": source_members or [],
                },
            )
            result["output_files"] = [
                {
                    "role": "material_fact_report",
                    "relative_path": "outputs/facts.json",
                    "media_type": "application/json",
                    "schema_id": "test.facts",
                    "schema_version": "1",
                }
            ]
        atomic_json(control_dir / "result.json", result)
    finally:
        stop.set()
        if thread is not None:
            thread.join(timeout=1.0)


if __name__ == "__main__":
    main()
