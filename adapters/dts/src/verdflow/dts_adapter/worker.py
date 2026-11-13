"""In-process DTS XML export Job driver."""

from __future__ import annotations

import errno
from contextlib import closing
import json
import time
import uuid
from datetime import timedelta
from typing import Any, BinaryIO, Iterator

from verdflow.contracts import (
    AssetAssignmentSetInfo,
    JobErrorInfo,
    JobInfo,
    OUTPUT_EXPORT_COMMAND_SCHEMA,
    OUTPUT_EXPORT_COMMAND_VERSION,
    PointSetInfo,
    PointSetKind,
)
from verdflow.core import JobRuntime, WorkerDescriptor
from verdflow.planting import (
    normalize_asset_selection,
    output_row_selected,
    paired_planting_rows,
    validate_effective_binding_snapshot,
)
from verdflow.project_store import JobLeaseConflictError

from .xml import _write_streamed_dts_xml


DTS_ADAPTER_ID = "verdflow.dts.xml"
DTS_ADAPTER_VERSION = "1"
DTS_XML_EXPORT_COMMAND = "verdflow.dts.export-xml"
DTS_EXPORT_OPERATION_LEASE = timedelta(minutes=5)


class _ExportCancelled(Exception):
    pass


_PlantingPairs = Iterator[tuple[dict[str, Any], dict[str, Any]]]


class DtsXmlExportWorker:
    """Render DTS output solely from immutable project Artifact references."""

    def __init__(self, runtime: JobRuntime, *, worker_id: str | None = None) -> None:
        self.runtime = runtime
        self._descriptor = WorkerDescriptor(
            worker_id=worker_id or f"dts_{uuid.uuid4().hex}",
            command_types=(DTS_XML_EXPORT_COMMAND,),
        )
        self._cancel_requests: set[str] = set()

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
            def check_cancel() -> None:
                if lease.job_id in self._cancel_requests:
                    raise _ExportCancelled()

            check_cancel()
            command = self._load_command(claimed.job.command.artifact_id)
            self.runtime.heartbeat(
                lease,
                progress=0.1,
                stage="loading_dts_export_inputs",
                lease_duration=DTS_EXPORT_OPERATION_LEASE,
            )
            last_heartbeat_at = time.monotonic()

            def report_progress(completed: int, total: int) -> None:
                nonlocal last_heartbeat_at
                check_cancel()
                now = time.monotonic()
                if completed < total and now - last_heartbeat_at < 5.0:
                    return
                self.runtime.heartbeat(
                    lease,
                    progress=0.2 + 0.65 * completed / max(total, 1),
                    stage="rendering_dts_xml",
                    lease_duration=DTS_EXPORT_OPERATION_LEASE,
                )
                last_heartbeat_at = now

            inputs = command.get("inputs", (command,))
            total = sum(self.runtime.store.get_point_set(item["point_set_id"]).point_count for item in inputs)

            def load_batches() -> Iterator[tuple[dict[str, Any], _PlantingPairs]]:
                scanned = 0
                for item in inputs:
                    check_cancel()
                    self.runtime.heartbeat(
                        lease,
                        lease_duration=DTS_EXPORT_OPERATION_LEASE,
                    )
                    point, assignment, effective = self._load_input(item)
                    selection = normalize_asset_selection(item.get("selected_asset_ids"))
                    selected = frozenset(selection) if selection is not None else None
                    require_hit = item.get("require_terrain_hit", False)
                    if type(require_hit) is not bool:
                        raise ValueError("DTS export terrain selection must be boolean")

                    def rows() -> _PlantingPairs:
                        nonlocal scanned
                        matched = 0
                        missing = set(selected or ())
                        with (
                            self.runtime.store.open_artifact(point.point_artifact_id) as points_file,
                            self.runtime.store.open_artifact(assignment.assignment_artifact_id) as assignment_file,
                            closing(paired_planting_rows(
                                points_file, assignment_file, point_count=point.point_count,
                                point_kind=point.kind.value, assignment_hash=assignment.semantic_hash,
                                target_type=assignment.target_type, scratch_root=self.runtime.store.root / "runtime",
                                on_rows=lambda: report_progress(scanned, total),
                            )) as pairs,
                        ):
                            for point_row, assignment_row in pairs:
                                scanned += 1
                                if missing:
                                    missing.discard(assignment_row["asset_id"])
                                if output_row_selected(point_row, assignment_row, selected, require_terrain_hit=require_hit):
                                    matched += 1
                                    yield point_row, assignment_row
                        if missing:
                            raise ValueError("Output references unknown assets: " + ", ".join(sorted(missing)))
                        if not matched and (selected is not None or require_hit):
                            raise ValueError("Output selection has no points or mismatched assignments")
                        report_progress(scanned, total)

                    with closing(rows()) as batch_rows:
                        yield effective, batch_rows

            def persist_progress() -> None:
                nonlocal last_heartbeat_at
                check_cancel()
                now = time.monotonic()
                if now - last_heartbeat_at >= 5.0:
                    self.runtime.heartbeat(lease, lease_duration=DTS_EXPORT_OPERATION_LEASE)
                    last_heartbeat_at = now

            record_count = 0

            def write_xml(stream: BinaryIO) -> None:
                nonlocal record_count, last_heartbeat_at
                with closing(load_batches()) as batches:
                    record_count = _write_streamed_dts_xml(stream, batches, tuple(command["offset_xyz"]))
                if record_count != command.get("expected_record_count", record_count):
                    raise ValueError("DTS export result count differs from selected inputs")
                check_cancel()
                self.runtime.heartbeat(
                    lease,
                    progress=0.9,
                    stage="persisting_dts_xml",
                    lease_duration=DTS_EXPORT_OPERATION_LEASE,
                )
                last_heartbeat_at = time.monotonic()

            output = self.runtime.store.write_artifact(
                write_xml,
                media_type="application/xml",
                schema_id="verdflow.dts-model-point-xml",
                schema_version="1",
                on_chunk=persist_progress,
            )
            result = {
                "schema": "verdflow.dts-export.result",
                "version": "1",
                "result_kind": "output_export",
                "producer": {
                    "id": DTS_ADAPTER_ID,
                    "version": DTS_ADAPTER_VERSION,
                },
                "record_count": record_count,
                "output_artifacts": [
                    {"role": "export", "artifact_id": output.artifact_id}
                ],
            }
            result_artifact = self.runtime.store.put_bytes(
                json.dumps(
                    result,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8"),
                media_type="application/vnd.verdflow.worker-result+json",
                schema_id="verdflow.dts-export.result",
                schema_version="1",
            )
            check_cancel()
            return self.runtime.succeed(
                lease,
                result_artifact_id=result_artifact.artifact_id,
            )
        except _ExportCancelled:
            try:
                self.runtime.request_cancel(lease.job_id)
                return self.runtime.acknowledge_cancel(lease)
            except JobLeaseConflictError:
                return None
        except JobLeaseConflictError:
            return None
        except (ValueError, KeyError, OSError, MemoryError) as error:
            disk_full = isinstance(error, OSError) and error.errno == errno.ENOSPC
            code = "DTS_EXPORT_DISK_FULL" if disk_full else "DTS_EXPORT_FAILED"
            message = str(error) or type(error).__name__
            try:
                return self.runtime.fail(lease, JobErrorInfo(code, message, retryable=disk_full))
            except JobLeaseConflictError:
                return None
        finally:
            self._cancel_requests.discard(lease.job_id)

    def reconcile(self) -> int:
        return 0

    def request_cancel(self, job_id: str) -> None:
        self._cancel_requests.add(job_id)

    def _load_command(self, artifact_id: str) -> dict[str, Any]:
        value = self._read_json(artifact_id)
        provider = value.get("provider")
        if (
            value.get("schema") != OUTPUT_EXPORT_COMMAND_SCHEMA
            or str(value.get("version")) != OUTPUT_EXPORT_COMMAND_VERSION
            or value.get("operation")
            not in {"export_output", "export_composition"}
            or provider
            != {"id": DTS_ADAPTER_ID, "version": DTS_ADAPTER_VERSION}
            or value.get("target_type") != "dts"
        ):
            raise ValueError("DTS export command schema is unsupported")
        if value["operation"] == "export_composition":
            inputs = value.get("inputs")
            if not isinstance(inputs, list) or not inputs:
                raise ValueError("DTS composition export inputs are required")
            for ordinal, item in enumerate(inputs):
                if not isinstance(item, dict) or item.get("ordinal") != ordinal:
                    raise ValueError("DTS composition export input order is invalid")
                self._validate_input(item)
        else:
            self._validate_input(value)
        offset = value.get("offset_xyz")
        if not isinstance(offset, list) or len(offset) != 3:
            raise ValueError("DTS export offset_xyz is invalid")
        return value

    def _validate_input(self, value: dict[str, Any]) -> None:
        for field in (
            "point_set_id",
            "points_artifact_id",
            "asset_assignment_set_id",
            "assignment_artifact_id",
            "effective_binding_snapshot_id",
            "effective_binding_artifact_id",
        ):
            self._required(value.get(field), field)

    def _load_input(
        self,
        command: dict[str, Any],
    ) -> tuple[PointSetInfo, AssetAssignmentSetInfo, dict[str, Any]]:
        point = self.runtime.store.get_point_set(command["point_set_id"])
        assignment = self.runtime.store.get_asset_assignment_set(
            command["asset_assignment_set_id"]
        )
        binding = self.runtime.store.get_effective_binding_snapshot(
            command["effective_binding_snapshot_id"]
        )
        if (
            point.point_artifact_id != command["points_artifact_id"]
            or assignment.assignment_artifact_id
            != command["assignment_artifact_id"]
            or binding.snapshot_artifact_id
            != command["effective_binding_artifact_id"]
        ):
            raise ValueError("DTS export command Artifact reference is stale")
        if (
            point.kind is not PointSetKind.ELEVATION_RESOLVED
            or assignment.point_set_id
            not in {point.point_set_id, point.parent_point_set_id}
            or assignment.effective_binding_snapshot_id
            != binding.effective_binding_snapshot_id
            or assignment.target_type != "dts"
            or binding.target_type != "dts"
            or assignment.point_count != point.point_count
        ):
            raise ValueError("DTS export project input lineage is inconsistent")
        effective = validate_effective_binding_snapshot(
            self._read_json(binding.snapshot_artifact_id)
        )
        if effective["semantic_hash"] != binding.semantic_hash:
            raise ValueError("DTS binding hash differs from its Core record")
        return point, assignment, effective

    def _read_json(self, artifact_id: str) -> dict[str, Any]:
        with self.runtime.store.open_artifact(artifact_id) as stream:
            value = json.load(stream)
        if not isinstance(value, dict):
            raise ValueError("DTS export Artifact must be a JSON object")
        return value

    @staticmethod
    def _required(value: object, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} is required")
        return value.strip()
