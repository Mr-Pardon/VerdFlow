"""In-process WorkerDriver for deterministic assignment resolution."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from verdflow.contracts import (
    ASSET_ASSIGNMENT_COMMAND,
    ASSET_ASSIGNMENT_COMMAND_SCHEMA,
    ASSET_ASSIGNMENT_COMMAND_VERSION,
    JobErrorInfo,
    JobInfo,
    JobLease,
)
from verdflow.planting import (
    canonical_json_bytes,
    resolve_persisted_asset_assignments,
)
from verdflow.project_store import JobLeaseConflictError

from .job_runtime import JobRuntime
from .workers import WorkerDescriptor


class _AssignmentCancelled(Exception):
    pass


class AssetAssignmentWorker:
    """Resolve immutable project artifacts without an external process."""

    def __init__(self, runtime: JobRuntime, *, worker_id: str | None = None) -> None:
        self.runtime = runtime
        self._descriptor = WorkerDescriptor(
            worker_id=worker_id or f"assignment_{uuid.uuid4().hex}",
            command_types=(ASSET_ASSIGNMENT_COMMAND,),
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
                return self._acknowledge_cancel(lease)
            command = self._load_command(claimed.job.command.artifact_id)
            self.runtime.heartbeat(
                lease,
                progress=0.1,
                stage="loading_assignment_inputs",
            )
            point_set, point_count = self._load_project_document(
                command["points_artifact_id"],
                command["point_set_id"],
                expected_point_artifact=True,
            )
            effective, _ = self._load_project_document(
                command["effective_binding_artifact_id"],
                command["effective_binding_snapshot_id"],
                expected_point_artifact=False,
            )
            if effective.get("target", {}).get("target_type") != command[
                "target_type"
            ]:
                raise ValueError(
                    "Assignment command target differs from the binding snapshot"
                )
            progress = self._progress_reporter(lease)
            assignments = resolve_persisted_asset_assignments(
                point_set,
                effective,
                random_seed=command["random_seed"],
                expected_point_count=point_count,
                progress=progress,
            )
            if lease.job_id in self._cancel_requests:
                return self._acknowledge_cancel(lease)
            self.runtime.heartbeat(
                lease,
                progress=0.9,
                stage="persisting_asset_assignments",
            )
            assignment_artifact = self.runtime.store.put_bytes(
                canonical_json_bytes(assignments),
                media_type="application/vnd.verdflow.asset-assignment-set+json",
                schema_id="verdflow.asset-assignment-set",
                schema_version="1",
            )
            result = {
                "schema": "verdflow.asset-assignment.result",
                "version": "1",
                "result_kind": "asset_assignment",
                "producer": {
                    "id": "verdflow.hash-weighted-assignment",
                    "version": "1",
                },
                "output_artifacts": [
                    {
                        "role": "assignments",
                        "artifact_id": assignment_artifact.artifact_id,
                    }
                ],
            }
            result_artifact = self.runtime.store.put_bytes(
                canonical_json_bytes(result),
                media_type="application/vnd.verdflow.worker-result+json",
                schema_id="verdflow.asset-assignment.result",
                schema_version="1",
            )
            return self.runtime.succeed(
                lease,
                result_artifact_id=result_artifact.artifact_id,
            )
        except _AssignmentCancelled:
            return self._acknowledge_cancel(lease)
        except JobLeaseConflictError:
            return None
        except (ValueError, KeyError, OSError, json.JSONDecodeError) as error:
            try:
                return self.runtime.fail(
                    lease,
                    JobErrorInfo(
                        "ASSET_ASSIGNMENT_FAILED",
                        str(error),
                        retryable=False,
                    ),
                )
            except JobLeaseConflictError:
                return None
        finally:
            self._cancel_requests.discard(lease.job_id)

    def reconcile(self) -> int:
        return 0

    def request_cancel(self, job_id: str) -> None:
        self._cancel_requests.add(job_id)

    def _acknowledge_cancel(self, lease: JobLease) -> JobInfo | None:
        try:
            self.runtime.request_cancel(lease.job_id)
            return self.runtime.acknowledge_cancel(lease)
        except JobLeaseConflictError:
            return None

    def _load_command(self, artifact_id: str) -> dict[str, Any]:
        value = self._read_json(artifact_id)
        if (
            value.get("schema") != ASSET_ASSIGNMENT_COMMAND_SCHEMA
            or str(value.get("version")) != ASSET_ASSIGNMENT_COMMAND_VERSION
            or value.get("operation") != "assign_assets"
        ):
            raise ValueError("Assignment command schema is unsupported")
        for field in (
            "point_set_id",
            "points_artifact_id",
            "effective_binding_snapshot_id",
            "effective_binding_artifact_id",
            "target_type",
        ):
            self._required_text(value.get(field), field)
        seed = value.get("random_seed")
        if (
            not isinstance(seed, int)
            or isinstance(seed, bool)
            or seed < 0
            or seed > 2**63 - 1
        ):
            raise ValueError("Assignment random_seed is invalid")
        return value

    def _load_project_document(
        self,
        artifact_id: str,
        record_id: str,
        *,
        expected_point_artifact: bool,
    ) -> tuple[dict[str, Any], int]:
        if expected_point_artifact:
            record = self.runtime.store.get_point_set(record_id)
            expected = record.point_artifact_id
            expected_schema = "verdflow.points"
            expected_version = "2"
            point_count = record.point_count
        else:
            record = self.runtime.store.get_effective_binding_snapshot(record_id)
            expected = record.snapshot_artifact_id
            expected_schema = "verdflow.effective-binding-snapshot"
            expected_version = "1"
            point_count = 0
        if expected != artifact_id:
            raise ValueError("Assignment command Artifact reference is stale")
        artifact = self.runtime.store.get_artifact(artifact_id)
        if (
            artifact.schema_id != expected_schema
            or artifact.schema_version != expected_version
        ):
            raise ValueError("Assignment input Artifact schema is unsupported")
        return self._read_json(artifact_id), point_count

    def _progress_reporter(self, lease: JobLease):
        last_heartbeat_at = time.monotonic()
        last_stage: str | None = None

        def report(stage: str, phase_progress: float) -> None:
            nonlocal last_heartbeat_at, last_stage
            if lease.job_id in self._cancel_requests:
                raise _AssignmentCancelled()
            now = time.monotonic()
            if (
                stage == last_stage
                and phase_progress < 1.0
                and now - last_heartbeat_at < 5.0
            ):
                return
            if stage == "resolving_asset_assignments":
                progress = 0.2 + 0.55 * phase_progress
            else:
                progress = 0.75 + 0.13 * phase_progress
            self.runtime.heartbeat(
                lease,
                progress=progress,
                stage=stage,
            )
            last_heartbeat_at = now
            last_stage = stage

        return report

    def _read_json(self, artifact_id: str) -> dict[str, Any]:
        with self.runtime.store.open_artifact(artifact_id) as stream:
            value = json.load(stream)
        if not isinstance(value, dict):
            raise ValueError("Assignment Artifact must be a JSON object")
        return value

    @staticmethod
    def _required_text(value: object, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} is required")
        return value.strip()
