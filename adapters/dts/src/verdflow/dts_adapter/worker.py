"""In-process DTS XML export Job driver."""

from __future__ import annotations

import json
import time
import uuid
from datetime import timedelta
from typing import Any

from verdflow.contracts import (
    JobErrorInfo,
    JobInfo,
    OUTPUT_EXPORT_COMMAND_SCHEMA,
    OUTPUT_EXPORT_COMMAND_VERSION,
)
from verdflow.core import JobRuntime, WorkerDescriptor
from verdflow.planting import (
    select_persisted_output_assets,
    validate_effective_binding_snapshot,
)

from .xml import _render_persisted_dts_xml_composition


DTS_ADAPTER_ID = "verdflow.dts.xml"
DTS_ADAPTER_VERSION = "1"
DTS_XML_EXPORT_COMMAND = "verdflow.dts.export-xml"
DTS_EXPORT_OPERATION_LEASE = timedelta(minutes=5)


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
            if lease.job_id in self._cancel_requests:
                return self.runtime.acknowledge_cancel(lease)
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

            if command["operation"] == "export_composition":
                inputs = []
                for index, item in enumerate(command["inputs"], start=1):
                    point_set, effective, assignments = self._load_input(item)
                    inputs.append((point_set, effective, assignments))
                    self.runtime.heartbeat(
                        lease,
                        progress=0.1 + 0.1 * index / len(command["inputs"]),
                        stage="loading_dts_export_inputs",
                        lease_duration=DTS_EXPORT_OPERATION_LEASE,
                    )
                    last_heartbeat_at = time.monotonic()

                document = _render_persisted_dts_xml_composition(
                    tuple(inputs),
                    tuple(command["offset_xyz"]),
                    progress=report_progress,
                )
            else:
                point_set, effective, assignments = self._load_input(command)
                document = _render_persisted_dts_xml_composition(
                    ((point_set, effective, assignments),),
                    tuple(command["offset_xyz"]),
                    progress=report_progress,
                )
            if lease.job_id in self._cancel_requests:
                return self.runtime.acknowledge_cancel(lease)
            self.runtime.heartbeat(
                lease,
                progress=0.9,
                stage="persisting_dts_xml",
                lease_duration=DTS_EXPORT_OPERATION_LEASE,
            )
            output = self.runtime.store.put_bytes(
                document.content,
                media_type="application/xml",
                schema_id="verdflow.dts-model-point-xml",
                schema_version="1",
            )
            result = {
                "schema": "verdflow.dts-export.result",
                "version": "1",
                "result_kind": "output_export",
                "producer": {
                    "id": DTS_ADAPTER_ID,
                    "version": DTS_ADAPTER_VERSION,
                },
                "record_count": document.record_count,
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
            return self.runtime.succeed(
                lease,
                result_artifact_id=result_artifact.artifact_id,
            )
        except (ValueError, KeyError, OSError, json.JSONDecodeError) as error:
            return self.runtime.fail(
                lease,
                JobErrorInfo("DTS_EXPORT_FAILED", str(error), retryable=False),
            )
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
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
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
            assignment.point_set_id
            not in {point.point_set_id, point.parent_point_set_id}
            or assignment.effective_binding_snapshot_id
            != binding.effective_binding_snapshot_id
            or assignment.target_type != "dts"
            or binding.target_type != "dts"
            or assignment.point_count != point.point_count
        ):
            raise ValueError("DTS export project input lineage is inconsistent")
        points, assignments = select_persisted_output_assets(
            self._read_json(point.point_artifact_id),
            self._read_json(assignment.assignment_artifact_id),
            command.get("selected_asset_ids"),
            expected_point_count=point.point_count,
            expected_assignment_hash=assignment.semantic_hash,
            require_terrain_hit=command.get("require_terrain_hit", False),
        )
        effective = validate_effective_binding_snapshot(
            self._read_json(binding.snapshot_artifact_id)
        )
        if effective["semantic_hash"] != binding.semantic_hash:
            raise ValueError("DTS binding hash differs from its Core record")
        return points, effective, assignments

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
