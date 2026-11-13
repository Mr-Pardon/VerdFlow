"""In-process Core Worker for one DTS Live Connector session."""

from __future__ import annotations

import json
import math
from threading import RLock
from time import monotonic
from typing import Any, BinaryIO, Callable, Protocol, Sequence
import uuid

from verdflow.contracts import (
    ELEVATION_SAMPLING_COMMAND_SCHEMA,
    ELEVATION_SAMPLING_COMMAND_VERSION,
    JobErrorInfo,
    JobInfo,
    REGION_EXTRACTION_COMMAND_SCHEMA,
    REGION_EXTRACTION_COMMAND_VERSION,
)
from verdflow.core import JobRuntime, WorkerDescriptor

from .live import (
    DtsElevationSample,
    DtsLiveElevationBatch,
    DtsLiveCommand,
    DtsLiveCorrelation,
    DtsLiveSelection,
)


DTS_LIVE_ADAPTER_ID = "verdflow.dts.live"
DTS_LIVE_ADAPTER_VERSION = "1"
DTS_LIVE_SCENE_INSPECTION_COMMAND = "verdflow.dts.live-inspect-scene"
DTS_LIVE_REGION_SELECTION_COMMAND = "verdflow.dts.live-select-region"
DTS_LIVE_PATH_SELECTION_COMMAND = "verdflow.dts.live-select-paths"
DTS_LIVE_ELEVATION_COMMAND = "verdflow.dts.live-sample-elevation"
DTS_LIVE_SUPPORTED_COMMANDS = (
    DTS_LIVE_SCENE_INSPECTION_COMMAND,
    DTS_LIVE_REGION_SELECTION_COMMAND,
    DTS_LIVE_PATH_SELECTION_COMMAND,
    DTS_LIVE_ELEVATION_COMMAND,
)
_ELEVATION_STREAM_ROWS = 16_384
_ELEVATION_WRITE_BATCH_ROWS = 1024


class DtsLiveWorkerPort(Protocol):
    connector_session_id: str

    def selection(
        self,
        operation_id: str,
        generation_token: int,
    ) -> DtsLiveSelection: ...

    def request_elevation(
        self,
        *,
        operation_id: str,
        generation_token: int,
        point_set_id: str,
        point_ids: Sequence[str],
        lines: Sequence[dict[str, object]],
        batches: Sequence[DtsLiveElevationBatch] = (),
    ) -> DtsLiveCommand: ...

    def wait_for_elevation(
        self,
        correlation: DtsLiveCorrelation,
        *,
        timeout_seconds: float,
    ) -> tuple[DtsElevationSample, ...] | None: ...

    def cancel(self) -> object: ...


class DtsLiveCoreWorker:
    """Translate typed DTS callbacks into canonical Core Worker results."""

    def __init__(
        self,
        runtime: JobRuntime,
        port: DtsLiveWorkerPort,
        *,
        worker_id: str | None = None,
        callback_poll_seconds: float = 1.0,
    ) -> None:
        if callback_poll_seconds <= 0:
            raise ValueError("callback_poll_seconds must be positive")
        self.runtime = runtime
        self.port = port
        self.callback_poll_seconds = callback_poll_seconds
        self._descriptor = WorkerDescriptor(
            worker_id=worker_id or f"dts_live_{uuid.uuid4().hex}",
            command_types=DTS_LIVE_SUPPORTED_COMMANDS,
        )
        self._cancel_requests: set[str] = set()
        self._cancel_lock = RLock()

    @property
    def descriptor(self) -> WorkerDescriptor:
        return self._descriptor

    def run_once(self) -> JobInfo | None:
        claimed = self.runtime.claim_next(
            self.descriptor.worker_id,
            command_types=self.descriptor.command_types,
        )
        if claimed is None:
            return None
        lease = claimed.lease
        try:
            if self._is_cancelled(lease.job_id):
                return self.runtime.acknowledge_cancel(lease)
            command = self._read_json(claimed.job.command.artifact_id)
            if claimed.job.command.command_type == DTS_LIVE_SCENE_INSPECTION_COMMAND:
                result_id = self._inspect_scene(command)
            elif claimed.job.command.command_type == DTS_LIVE_REGION_SELECTION_COMMAND:
                result_id = self._select_region(command)
            elif claimed.job.command.command_type == DTS_LIVE_PATH_SELECTION_COMMAND:
                result_id = self._select_paths(command)
            elif claimed.job.command.command_type == DTS_LIVE_ELEVATION_COMMAND:
                result_id = self._sample_elevation(command, lease)
            else:
                raise ValueError("DTS Live command type is unsupported")
            if self._is_cancelled(lease.job_id):
                self.port.cancel()
                return self.runtime.acknowledge_cancel(lease)
            return self.runtime.succeed(
                lease,
                result_artifact_id=result_id,
            )
        except _DtsLiveCancelled:
            return self.runtime.acknowledge_cancel(lease)
        except (ValueError, KeyError, OSError, json.JSONDecodeError) as error:
            return self.runtime.fail(
                lease,
                JobErrorInfo(
                    "DTS_LIVE_WORKER_FAILED",
                    str(error) or type(error).__name__,
                    retryable=False,
                ),
            )
        finally:
            with self._cancel_lock:
                self._cancel_requests.discard(lease.job_id)

    def reconcile(self) -> int:
        return 0

    def request_cancel(self, job_id: str) -> None:
        with self._cancel_lock:
            self._cancel_requests.add(job_id)

    def _inspect_scene(self, command: dict[str, Any]) -> str:
        self._validate_custom_command(command, "inspect_scene")
        source_id = _text(command, "source_id")
        manifest = self._put_json(
            {
                "schema": "verdflow.scene-inspection-manifest",
                "version": "1",
                "coordinate_frame": {
                    "name": "DTS scene world",
                    "handedness": "right-handed",
                    "up_axis": "+Z",
                    "linear_unit": "meter",
                    "crs_kind": "unknown",
                    "crs_definition": None,
                    "local_origin": [0.0, 0.0, 0.0],
                    "offset_applied": False,
                },
            },
            media_type="application/vnd.verdflow.scene-inspection-manifest+json",
            schema_id="verdflow.scene-inspection-manifest",
        )
        facts = self._put_json(
            {
                "schema": "verdflow.dts-live-scene-facts",
                "version": "1",
                "materials": [],
            },
            media_type="application/vnd.verdflow.material-facts+json",
            schema_id="verdflow.dts-live-scene-facts",
        )
        return self._result(
            "scene_inspection",
            source_id=source_id,
            outputs=(
                ("scene_manifest", manifest),
                ("material_fact_report", facts),
            ),
            operation="inspect_scene",
        )

    def _select_paths(self, command: dict[str, Any]) -> str:
        if (
            command.get("schema") != "verdflow.path-selection.command"
            or command.get("version") != "1"
            or command.get("operation") != "select_paths"
        ):
            raise ValueError("路径选择命令格式不匹配")
        context = self._live_context(command.get("context"))
        selection = self.port.selection(
            context["operation_id"], context["generation_token"]
        )
        if selection.regions or not selection.paths:
            raise ValueError("路径策略只接受开放折线")
        document = {
            "schema": "verdflow.paths",
            "version": "1",
            "coordinate_space": "source_scene_world",
            "offset_applied": False,
            "path_count": len(selection.paths),
            "paths": [
                {
                    "id": path.path_id,
                    "name": path.name,
                    "coordinates": [list(point) for point in path.coordinates],
                    "bbox": list(path.bbox),
                }
                for path in selection.paths
            ],
        }
        from verdflow.planting import validate_path_document

        validate_path_document(document)
        artifact = self._put_json(
            document,
            media_type="application/vnd.verdflow.paths+json",
            schema_id="verdflow.paths",
        )
        return self._result(
            "path_selection",
            source_id=_text(command, "source_id"),
            outputs=(("paths", artifact),),
            operation="select_paths",
        )

    def _select_region(self, command: dict[str, Any]) -> str:
        self._validate_provider_command(
            command,
            schema=REGION_EXTRACTION_COMMAND_SCHEMA,
            version=REGION_EXTRACTION_COMMAND_VERSION,
            operation="extract_regions",
        )
        context = self._live_context(command.get("import_settings"))
        selection = self.port.selection(
            context["operation_id"],
            context["generation_token"],
        )
        regions = []
        region_metrics = []
        for region in selection.regions:
            polygon = [list(point) for point in region.coordinates]
            boundary = [*polygon, list(polygon[0])]
            hole_rings = [[list(point) for point in ring] for ring in region.holes]
            boundaries = [boundary, *[[*ring, list(ring[0])] for ring in hole_rings]]
            bbox = list(region.bbox)
            regions.append({
                "id": region.region_id,
                "name": region.name,
                "label": region.name,
                "tags": "dts-live,user-selection",
                "region_type": "region",
                "source_object": "",
                "coordinate_space": "source_scene_world",
                "offset_applied": False,
                "bbox": bbox,
                "footprint_boundary_xyz": boundaries,
                "mesh": "",
                "extraction_method": "dts_user_polygon",
            })
            region_metrics.append({
                "id": region.region_id,
                "bbox": bbox,
                "bbox_size": [bbox[i + 3] - bbox[i] for i in range(3)],
                "footprint_area_xy": _polygon_area_xy(polygon) - sum(_polygon_area_xy(ring) for ring in hole_rings),
                "boundary_perimeter_xy": sum(_polygon_perimeter_xy(ring) for ring in [polygon, *hole_rings]),
                "boundary_loop_count": len(boundaries),
                "is_near_horizontal": True,
            })
        geometry = self._put_json(
            {
                "schema": "verdflow.regions",
                "version": "1",
                "coordinate_space": "source_scene_world",
                "offset_applied": False,
                "source_name": "DTS Live Scene",
                "region_count": len(regions),
                "regions": regions,
            },
            media_type="application/vnd.verdflow.regions+json",
            schema_id="verdflow.regions",
        )
        metrics = self._put_json(
            {
                "schema": "verdflow.region-metrics",
                "version": "1",
                "coordinate_space": "source_scene_world",
                "offset_applied": False,
                "region_count": len(regions),
                "regions": region_metrics,
            },
            media_type="application/vnd.verdflow.region-metrics+json",
            schema_id="verdflow.region-metrics",
        )
        return self._result(
            "region_extraction",
            source_id=_text(command, "source_id"),
            outputs=(("regions", geometry), ("geometry_metrics", metrics)),
            operation="extract_regions",
        )

    def _sample_elevation(self, command: dict[str, Any], lease: Any) -> str:
        self._validate_provider_command(
            command,
            schema=ELEVATION_SAMPLING_COMMAND_SCHEMA,
            version="2" if command.get("version") == "2" else ELEVATION_SAMPLING_COMMAND_VERSION,
            operation="sample_elevation",
        )
        context = self._live_context(command.get("options"))
        if command.get("version") == "2":
            from verdflow.contracts import SamplingBinding
            binding = SamplingBinding.from_document(command["sampling_binding"])
            source = binding.source
            if (source.kind != "session" or source.connector_session_id != context["connector_session_id"]
                    or source.operation_id != context["operation_id"]
                    or source.generation_token != context["generation_token"]
                    or source.scene_revision_id != command.get("scene_revision_id")
                    or source.source_id != command.get("source_id")):
                raise ValueError("DTS Live session differs from the frozen sampling binding")
        parent = self.runtime.store.get_point_set(
            _text(command, "parent_point_set_id")
        )
        if parent.point_artifact_id != _text(command, "points_artifact_id"):
            raise ValueError("DTS Live PointSet Artifact reference is stale")
        points_document = self._read_json(parent.point_artifact_id)
        points = points_document.get("points")
        if (
            points_document.get("schema") != "verdflow.points"
            or str(points_document.get("version")) not in {"2", "3"}
            or points_document.get("kind") != "generated"
            or points_document.get("offset_applied") is not False
            or not isinstance(points, list)
            or not points
        ):
            raise ValueError("DTS Live elevation input is not a generated PointSet v2")
        point_ids: list[str] = []
        lines: list[dict[str, object]] = []
        selection = self.port.selection(context["operation_id"], context["generation_token"])
        path_input = bool(selection.paths)
        geometries = selection.paths if path_input else selection.regions
        key = "path_id" if path_input else "region_id"
        by_region: dict[str, list[dict[str, Any]]] = {
            getattr(region, key): [] for region in geometries
        }
        for point in points:
            if not isinstance(point, dict) or point.get(key) not in by_region:
                raise ValueError("DTS Live point must belong to a selected region")
            by_region[point[key]].append(point)
        batches: list[DtsLiveElevationBatch] = []
        shared_sample_ids: dict[str, str] = {}
        half_range = _positive_float(context.get("ray_half_range", 5000.0))
        for region in geometries:
            group = by_region[getattr(region, key)]
            if not group:
                continue
            start = len(point_ids)
            # Keep each selection independent, even when its rays overlap another selection.
            first_sample_ids: dict[tuple[float, float, float], str] = {}
            for point in group:
                point_id = _text(point, "id")
                position = _vector3(point.get("generated_position"), "generated_position")
                sample_id = first_sample_ids.setdefault(position, point_id)
                if sample_id != point_id:
                    shared_sample_ids[point_id] = sample_id
                    continue
                x, y, z = position
                point_ids.append(point_id)
                lines.append({"start": [x, y, z + half_range], "end": [x, y, z - half_range]})
            first_sample_ids.clear()
            batches.append(
                DtsLiveElevationBatch(
                    None if path_input else region.region_id,
                    region.name,
                    region.bbox,
                    start,
                    len(point_ids) - start,
                    path_id=region.path_id if path_input else None,
                )
            )
        correlation = self.port.request_elevation(
            operation_id=context["operation_id"],
            generation_token=context["generation_token"],
            point_set_id=parent.point_set_id,
            point_ids=point_ids,
            lines=lines,
            batches=batches,
        ).correlation
        del point_ids, lines, batches, by_region
        samples = None
        while samples is None:
            if self._is_cancelled(lease.job_id):
                self.port.cancel()
                raise _DtsLiveCancelled
            samples = self.port.wait_for_elevation(
                correlation,
                timeout_seconds=self.callback_poll_seconds,
            )
            if samples is None:
                self.runtime.heartbeat(
                    lease,
                    progress=0.5,
                    stage="waiting_dts_elevation",
                )
        by_id = {sample.point_id: sample for sample in samples}
        if not any(sample.position is not None for sample in samples):
            raise ValueError("All selected regions failed elevation sampling; no XML was generated")
        points_document["kind"] = "elevation_resolved"
        points_document["resolution_policy"] = "sample_hit_else_generated"
        last_heartbeat = monotonic()

        def check_progress() -> None:
            nonlocal last_heartbeat
            if self._is_cancelled(lease.job_id):
                self.port.cancel()
                raise _DtsLiveCancelled
            now = monotonic()
            if now - last_heartbeat >= 5.0:
                self.runtime.heartbeat(lease, progress=0.8, stage="persisting_dts_elevation")
                last_heartbeat = now

        for index, point in enumerate(points):
            if index % _ELEVATION_WRITE_BATCH_ROWS == 0:
                check_progress()
            point_id = point["id"]
            sample = by_id[shared_sample_ids.get(point_id, point_id)]
            if sample.position is None:
                point["terrain_sample"] = {"hit": False, "target": "dts_region_sampling_failed"}
                point["resolved_position"] = point["generated_position"]
                continue
            position = list(sample.position)
            point["terrain_sample"] = {
                "hit": True,
                "position": position,
                "normal": [0.0, 0.0, 1.0],
                "target": "dts_lines_intersect",
            }
            point["resolved_position"] = position
        artifact_options = {
            "media_type": "application/vnd.verdflow.points+json",
            "schema_id": "verdflow.points",
            "schema_version": points_document["version"],
        }
        check_progress()
        if len(points) < _ELEVATION_STREAM_ROWS:
            point_artifact = self._put_json(points_document, **artifact_options)
        else:
            point_artifact = self.runtime.store.write_artifact(
                lambda stream: _write_elevation_json(stream, points_document, check_progress),
                on_chunk=check_progress,
                **artifact_options,
            ).artifact_id
        return self._result(
            "elevation_sampling",
            source_id=_text(command, "source_id"),
            outputs=(("points", point_artifact),),
            operation="sample_elevation",
        )

    def _validate_custom_command(
        self,
        command: dict[str, Any],
        operation: str,
    ) -> None:
        if (
            command.get("schema") != "verdflow.dts-live.command"
            or str(command.get("version")) != "1"
            or command.get("operation") != operation
        ):
            raise ValueError("DTS Live custom command is unsupported")
        context = command.get("context")
        if not isinstance(context, dict):
            raise ValueError("DTS Live connector context is required")
        if _text(context, "connector_session_id") != self.port.connector_session_id:
            raise ValueError("DTS Live connector session does not match the Worker")

    def _live_context(self, value: object) -> dict[str, Any]:
        context = _live_context(value)
        if context["connector_session_id"] != self.port.connector_session_id:
            raise ValueError("DTS Live connector session does not match the Worker")
        return context

    @staticmethod
    def _validate_provider_command(
        command: dict[str, Any],
        *,
        schema: str,
        version: str,
        operation: str,
    ) -> None:
        if (
            command.get("schema") != schema
            or str(command.get("version")) != version
            or command.get("operation") != operation
            or command.get("provider")
            != {"id": DTS_LIVE_ADAPTER_ID, "version": DTS_LIVE_ADAPTER_VERSION}
        ):
            raise ValueError("DTS Live provider command is unsupported")

    def _result(
        self,
        result_kind: str,
        *,
        source_id: str,
        outputs: tuple[tuple[str, str], ...],
        operation: str,
    ) -> str:
        result_schema = (
            "verdflow.path-selection.result"
            if result_kind == "path_selection"
            else "verdflow.dts-live.result"
        )
        return self._put_json(
            {
                "schema": result_schema,
                "version": "1",
                "status": "succeeded",
                "operation": operation,
                "result_kind": result_kind,
                "source_id": source_id,
                "producer": {
                    "id": DTS_LIVE_ADAPTER_ID,
                    "version": DTS_LIVE_ADAPTER_VERSION,
                },
                "output_artifacts": [
                    {"role": role, "artifact_id": artifact_id}
                    for role, artifact_id in outputs
                ],
            },
            media_type="application/vnd.verdflow.worker-result+json",
            schema_id=result_schema,
        )

    def _put_json(
        self,
        document: dict[str, Any],
        *,
        media_type: str,
        schema_id: str,
        schema_version: str = "1",
    ) -> str:
        artifact = self.runtime.store.put_bytes(
            _json_bytes(document),
            media_type=media_type,
            schema_id=schema_id,
            schema_version=schema_version,
        )
        return artifact.artifact_id

    def _read_json(self, artifact_id: str) -> dict[str, Any]:
        with self.runtime.store.open_artifact(artifact_id) as stream:
            value = json.load(stream)
        if not isinstance(value, dict):
            raise ValueError("DTS Live Artifact must be an object")
        return value

    def _is_cancelled(self, job_id: str) -> bool:
        with self._cancel_lock:
            return job_id in self._cancel_requests


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _write_elevation_json(stream: BinaryIO, document: dict[str, Any], check_progress: Callable[[], None]) -> None:
    # Keep the exact compact JSON used by the small-result path, including float spelling.
    stream.write(b"{")
    for ordinal, key in enumerate(sorted(document)):
        stream.write((b"," if ordinal else b"") + _json_bytes(key) + b":")
        if key != "points":
            stream.write(_json_bytes(document[key]))
            continue
        points = document[key]
        stream.write(b"[")
        for start in range(0, len(points), _ELEVATION_WRITE_BATCH_ROWS):
            check_progress()
            if start:
                stream.write(b",")
            stream.write(_json_bytes(points[start:start + _ELEVATION_WRITE_BATCH_ROWS])[1:-1])
        stream.write(b"]")
    stream.write(b"}")


def _live_context(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("DTS Live context is required")
    if _text(value, "connector_session_id") == "":
        raise ValueError("connector_session_id is required")
    generation = value.get("generation_token")
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
        raise ValueError("generation_token must be a positive integer")
    return {
        **value,
        "connector_session_id": _text(value, "connector_session_id"),
        "operation_id": _text(value, "operation_id"),
        "generation_token": generation,
    }


class _DtsLiveCancelled(Exception):
    """Internal control flow for a cooperative DTS callback cancellation."""


def _text(document: dict[str, Any], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value.strip()


def _vector3(value: object, label: str) -> tuple[float, float, float]:
    if (
        not isinstance(value, list)
        or len(value) != 3
        or not all(
            isinstance(item, (int, float))
            and not isinstance(item, bool)
            and math.isfinite(float(item))
            for item in value
        )
    ):
        raise ValueError(f"{label} must contain three finite numbers")
    return float(value[0]), float(value[1]), float(value[2])


def _positive_float(value: object) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or float(value) <= 0
    ):
        raise ValueError("ray_half_range must be a positive finite number")
    return float(value)


def _bbox(points: Sequence[Sequence[float]]) -> list[float]:
    return [
        min(point[0] for point in points),
        min(point[1] for point in points),
        min(point[2] for point in points),
        max(point[0] for point in points),
        max(point[1] for point in points),
        max(point[2] for point in points),
    ]


def _polygon_area_xy(points: Sequence[Sequence[float]]) -> float:
    return abs(
        sum(
            first[0] * second[1] - second[0] * first[1]
            for first, second in zip(points, (*points[1:], points[0]), strict=True)
        )
    ) * 0.5


def _polygon_perimeter_xy(points: Sequence[Sequence[float]]) -> float:
    return sum(
        math.hypot(second[0] - first[0], second[1] - first[1])
        for first, second in zip(points, (*points[1:], points[0]), strict=True)
    )
