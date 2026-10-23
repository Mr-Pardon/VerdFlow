"""Supervise geospatial Jobs through an isolated Python subprocess."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from verdflow.contracts import (
    ELEVATION_SAMPLING_COMMAND_SCHEMA,
    ELEVATION_SAMPLING_COMMAND_VERSION,
    JobErrorInfo,
    JobInfo,
    JobStatus,
    REGION_SOURCE_COMMAND_SCHEMA,
    REGION_SOURCE_COMMAND_VERSION,
    SourceKind,
    WorkerProcessInfo,
    WorkerProcessStatus,
)
from verdflow.core import JobRuntime, WorkerDescriptor
from verdflow.project_store import ProcessStatus, SystemProcessInspector
from verdflow.worker_runtime import ProcessTreeGuard, terminate_process_by_pid

from .protocol import (
    GEOSPATIAL_ADAPTER_ID,
    GEOSPATIAL_PREPARE_REGIONS_COMMAND,
    GEOSPATIAL_PROTOCOL_VERSION,
    GEOSPATIAL_SAMPLE_ELEVATION_COMMAND,
    GEOSPATIAL_SUPPORTED_COMMANDS,
    GeospatialWorkerConfig,
)


class GeospatialWorkerError(RuntimeError):
    """Raised when the supervised Worker protocol is invalid."""


class GeospatialWorkerSupervisor:
    """Own process lifetime while Core owns durable Job state."""

    descriptor = WorkerDescriptor(
        worker_id="geospatial-worker",
        command_types=GEOSPATIAL_SUPPORTED_COMMANDS,
    )

    def __init__(
        self,
        runtime: JobRuntime,
        config: GeospatialWorkerConfig,
        *,
        worker_id: str | None = None,
    ) -> None:
        config.validate()
        self.runtime = runtime
        self.config = config
        self.worker_id = worker_id or f"geospatial_{uuid.uuid4().hex}"
        self._cancel_requests: set[str] = set()
        self._cancel_lock = threading.Lock()
        config.runtime_root.mkdir(parents=True, exist_ok=True)
        config.cache_root.mkdir(parents=True, exist_ok=True)

    def request_cancel(self, job_id: str) -> None:
        with self._cancel_lock:
            self._cancel_requests.add(job_id)

    def run_once(self) -> JobInfo | None:
        claimed = self.runtime.claim_next(
            self.worker_id,
            command_types=GEOSPATIAL_SUPPORTED_COMMANDS,
        )
        if claimed is None:
            return None
        return self._run_claimed(claimed)

    def reconcile(self) -> int:
        inspector = SystemProcessInspector()
        recovered = 0
        for record in self.runtime.store.list_active_worker_processes():
            if record.adapter_id != GEOSPATIAL_ADAPTER_ID:
                continue
            probe = inspector.probe(record.host, record.pid)
            if probe.status is ProcessStatus.UNKNOWN:
                raise GeospatialWorkerError(
                    "Recorded geospatial process identity cannot be inspected"
                )
            status = WorkerProcessStatus.LOST
            exit_code: int | None = None
            if (
                probe.status is ProcessStatus.ALIVE
                and probe.identity is not None
                and probe.identity.started_at_token == record.started_at_token
            ):
                terminate_process_by_pid(record.pid)
                status = WorkerProcessStatus.TERMINATED
                exit_code = 1
            self.runtime.store.finish_worker_process(
                record.job_id,
                record.attempt_number,
                status=status,
                finished_at=_now_text(),
                exit_code=exit_code,
            )
            recovered += 1
        return recovered

    def _run_claimed(self, claimed: Any) -> JobInfo:
        lease = claimed.lease
        attempt_dir = self.config.runtime_root / (
            f"{lease.job_id}-{lease.attempt_count}-{uuid.uuid4().hex[:8]}"
        )
        output_dir = attempt_dir / "outputs"
        attempt_dir.mkdir(parents=True)
        output_dir.mkdir()
        command_path = attempt_dir / "command.json"
        result_path = attempt_dir / "result.json"
        stdout_path = attempt_dir / "stdout.log"
        stderr_path = attempt_dir / "stderr.log"
        process: subprocess.Popen[bytes] | None = None
        guard = ProcessTreeGuard()
        process_registered = False
        terminated = False
        try:
            self.runtime.heartbeat(lease, progress=0.05, stage="preparing_inputs")
            command = self._prepare_command(
                claimed.job.command.artifact_id,
                claimed.job.command.command_type,
            )
            _write_json(command_path, command)
            entry = Path(__file__).with_name("worker_entry.py")
            with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                process = subprocess.Popen(
                    [
                        str(self.config.python_executable),
                        "-I",
                        str(entry),
                        "--command",
                        str(command_path),
                        "--output-dir",
                        str(output_dir),
                        "--result",
                        str(result_path),
                    ],
                    cwd=attempt_dir,
                    env=_minimal_environment(attempt_dir),
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    **guard.popen_options(),
                )
                try:
                    guard.attach(process)
                except Exception:
                    process.kill()
                    process.wait(timeout=self.config.termination_grace_seconds)
                    raise
                identity = self._child_identity(process.pid)
                self.runtime.store.register_worker_process(
                    WorkerProcessInfo(
                        job_id=lease.job_id,
                        attempt_number=lease.attempt_count,
                        adapter_id=GEOSPATIAL_ADAPTER_ID,
                        host=identity.host,
                        pid=identity.pid,
                        started_at_token=identity.started_at_token,
                        status=WorkerProcessStatus.RUNNING,
                        started_at=_now_text(),
                        heartbeat_at=None,
                        finished_at=None,
                        exit_code=None,
                    )
                )
                process_registered = True
                started = time.monotonic()
                last_heartbeat = started
                while process.poll() is None:
                    now = time.monotonic()
                    if self._is_cancelled(lease.job_id):
                        if self.runtime.get(lease.job_id).status is JobStatus.RUNNING:
                            self.runtime.request_cancel(lease.job_id)
                        terminated = True
                        guard.terminate(
                            process,
                            grace_seconds=self.config.termination_grace_seconds,
                        )
                        break
                    if now - started >= self.config.timeout_seconds:
                        terminated = True
                        guard.terminate(
                            process,
                            grace_seconds=self.config.termination_grace_seconds,
                        )
                        raise GeospatialWorkerError(
                            "Geospatial worker exceeded its execution timeout"
                        )
                    if now - last_heartbeat >= self.config.heartbeat_interval_seconds:
                        self.runtime.heartbeat(
                            lease,
                            progress=0.5,
                            stage="processing_geospatial_data",
                        )
                        self.runtime.store.heartbeat_worker_process(
                            lease.job_id,
                            lease.attempt_count,
                            _now_text(),
                        )
                        last_heartbeat = now
                    time.sleep(self.config.poll_interval_seconds)
                if process.poll() is None:
                    process.wait(timeout=self.config.termination_grace_seconds)
            if process_registered:
                self.runtime.store.finish_worker_process(
                    lease.job_id,
                    lease.attempt_count,
                    status=(
                        WorkerProcessStatus.TERMINATED
                        if terminated
                        else WorkerProcessStatus.EXITED
                    ),
                    finished_at=_now_text(),
                    exit_code=process.returncode,
                )
                process_registered = False
            if self._is_cancelled(lease.job_id):
                return self.runtime.acknowledge_cancel(lease)
            result = _read_json(result_path)
            if process.returncode != 0 or result.get("status") != "succeeded":
                message = str(
                    result.get("message")
                    or _tail(stderr_path)
                    or f"process exited with code {process.returncode}"
                )
                return self.runtime.fail(
                    lease,
                    JobErrorInfo(
                        code="GEOSPATIAL_OPERATION_FAILED",
                        message=message,
                        retryable=False,
                        diagnostic_artifact_id=self._diagnostic(
                            stdout_path,
                            stderr_path,
                            message,
                            process.returncode,
                        ),
                    ),
                )
            expected_operation = _operation(claimed.job.command.command_type)
            if result.get("operation") != expected_operation:
                raise GeospatialWorkerError("Worker result operation differs")
            artifact_id = self._register_result(
                result,
                output_dir,
                job_id=lease.job_id,
                attempt_number=lease.attempt_count,
            )
            self.runtime.heartbeat(lease, progress=0.95, stage="persisting_result")
            return self.runtime.succeed(lease, result_artifact_id=artifact_id)
        except Exception as error:
            if process is not None and process.poll() is None:
                terminated = True
                guard.terminate(
                    process,
                    grace_seconds=self.config.termination_grace_seconds,
                )
            if process_registered:
                self.runtime.store.finish_worker_process(
                    lease.job_id,
                    lease.attempt_count,
                    status=WorkerProcessStatus.TERMINATED,
                    finished_at=_now_text(),
                    exit_code=(process.returncode if process is not None else None),
                )
            return self.runtime.fail(
                lease,
                JobErrorInfo(
                    code="GEOSPATIAL_SUPERVISOR_FAILED",
                    message=str(error),
                    retryable=False,
                    diagnostic_artifact_id=self._diagnostic(
                        stdout_path,
                        stderr_path,
                        str(error),
                        process.returncode if process is not None else None,
                    ),
                ),
            )
        finally:
            guard.close()
            with self._cancel_lock:
                self._cancel_requests.discard(lease.job_id)
            shutil.rmtree(attempt_dir, ignore_errors=True)

    def _prepare_command(
        self,
        command_artifact_id: str,
        command_type: str,
    ) -> dict[str, Any]:
        with self.runtime.store.open_artifact(command_artifact_id) as stream:
            command = json.load(stream)
        if not isinstance(command, dict):
            raise GeospatialWorkerError("Geospatial command must be an object")
        operation = _operation(command_type)
        expected_schema = (
            REGION_SOURCE_COMMAND_SCHEMA
            if operation == "prepare_regions"
            else ELEVATION_SAMPLING_COMMAND_SCHEMA
        )
        expected_version = (
            REGION_SOURCE_COMMAND_VERSION
            if operation == "prepare_regions"
            else ELEVATION_SAMPLING_COMMAND_VERSION
        )
        if (
            command.get("schema") != expected_schema
            or str(command.get("version")) != expected_version
            or command.get("operation") != operation
        ):
            raise GeospatialWorkerError("Geospatial command schema is unsupported")
        source_id = _required_text(command, "source_id")
        source = self.runtime.store.get_source(source_id)
        if source.kind is not SourceKind.ASSEMBLY:
            raise GeospatialWorkerError("Geospatial source must be an Assembly")
        if _required_text(command, "source_artifact_id") != source.artifact_id:
            raise GeospatialWorkerError("Geospatial Source Artifact differs")
        prepared = dict(command)
        prepared["sources"] = []
        for member in self.runtime.store.list_source_assembly_members(source_id):
            leaf = self.runtime.store.get_source(member.source_id)
            if leaf.kind not in {
                SourceKind.SHAPEFILE,
                SourceKind.GEOJSON,
                SourceKind.GEOTIFF,
            }:
                raise GeospatialWorkerError(
                    f"Unsupported geospatial Source kind: {leaf.kind.value}"
                )
            prepared["sources"].append(
                {
                    "source_member_id": member.source_member_id,
                    "source_id": leaf.source_id,
                    "kind": leaf.kind.value,
                    "original_name": leaf.original_name,
                    "source_path": str(self._cache_artifact(leaf.artifact_id, leaf.kind)),
                    "source_to_scene_matrix_row_major": list(
                        member.source_to_scene_matrix_row_major
                    ),
                }
            )
        if operation == "sample_elevation":
            points_artifact_id = _required_text(command, "points_artifact_id")
            points_artifact = self.runtime.store.get_artifact(points_artifact_id)
            if (
                points_artifact.schema_id != "verdflow.points"
                or points_artifact.schema_version != "2"
            ):
                raise GeospatialWorkerError("Elevation input must be PointSet v2")
            prepared["points_path"] = str(
                self._cache_artifact_by_ref(points_artifact, ".json")
            )
        return prepared

    def _cache_artifact(self, artifact_id: str, kind: SourceKind) -> Path:
        extensions = {
            SourceKind.SHAPEFILE: ".zip",
            SourceKind.GEOJSON: ".geojson",
            SourceKind.GEOTIFF: ".tif",
        }
        artifact = self.runtime.store.get_artifact(artifact_id)
        return self._cache_artifact_by_ref(artifact, extensions[kind])

    def _cache_artifact_by_ref(self, artifact: Any, extension: str) -> Path:
        digest = artifact.content_hash.removeprefix("sha256:")
        destination = self.config.cache_root / f"{digest}{extension}"
        if destination.is_file() and destination.stat().st_size == artifact.byte_size:
            return destination
        destination.unlink(missing_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        self.runtime.store.materialize_artifact(artifact.artifact_id, temporary)
        os.replace(temporary, destination)
        return destination

    def _register_result(
        self,
        result: dict[str, Any],
        output_dir: Path,
        *,
        job_id: str,
        attempt_number: int,
    ) -> str:
        descriptors = result.get("output_files")
        if not isinstance(descriptors, list):
            raise GeospatialWorkerError("Worker output_files must be an array")
        outputs: list[dict[str, Any]] = []
        roles: set[str] = set()
        root = output_dir.resolve()
        for descriptor in descriptors:
            if not isinstance(descriptor, dict):
                raise GeospatialWorkerError("Worker output descriptor is invalid")
            role = _required_text(descriptor, "role")
            if role in roles:
                raise GeospatialWorkerError(f"Duplicate output role: {role}")
            roles.add(role)
            path = (output_dir / _required_text(descriptor, "relative_path")).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise GeospatialWorkerError(f"Worker output is missing: {role}")
            artifact = self.runtime.store.put_file(
                path,
                media_type=_required_text(descriptor, "media_type"),
                schema_id=_required_text(descriptor, "schema_id"),
                schema_version=_required_text(descriptor, "schema_version"),
            )
            outputs.append(
                {
                    "role": role,
                    "artifact_id": artifact.artifact_id,
                    "content_hash": artifact.content_hash,
                    "byte_size": artifact.byte_size,
                    "media_type": artifact.media_type,
                    "schema_id": artifact.schema_id,
                    "schema_version": artifact.schema_version,
                }
            )
        normalized = {
            "schema": "verdflow.geospatial-worker.result",
            "version": GEOSPATIAL_PROTOCOL_VERSION,
            "result_kind": (
                "region_source"
                if result.get("operation") == "prepare_regions"
                else "elevation_sampling"
            ),
            "source_id": _required_text(result, "source_id"),
            "job_id": job_id,
            "attempt_number": attempt_number,
            "producer": {
                "id": GEOSPATIAL_ADAPTER_ID,
                "version": GEOSPATIAL_PROTOCOL_VERSION,
            },
            "output_artifacts": outputs,
        }
        artifact = self.runtime.store.put_bytes(
            json.dumps(normalized, ensure_ascii=False, sort_keys=True).encode("utf-8"),
            media_type="application/vnd.verdflow.geospatial-worker-result+json",
            schema_id="verdflow.geospatial-worker.result",
            schema_version=GEOSPATIAL_PROTOCOL_VERSION,
        )
        return artifact.artifact_id

    def _diagnostic(
        self,
        stdout_path: Path,
        stderr_path: Path,
        message: str,
        exit_code: int | None,
    ) -> str:
        artifact = self.runtime.store.put_bytes(
            json.dumps(
                {
                    "schema": "verdflow.geospatial-worker.diagnostic",
                    "version": "1",
                    "message": message,
                    "exit_code": exit_code,
                    "stdout_tail": _tail(stdout_path),
                    "stderr_tail": _tail(stderr_path),
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8"),
            media_type="application/vnd.verdflow.geospatial-worker-diagnostic+json",
            schema_id="verdflow.geospatial-worker.diagnostic",
            schema_version="1",
        )
        return artifact.artifact_id

    def _is_cancelled(self, job_id: str) -> bool:
        with self._cancel_lock:
            return job_id in self._cancel_requests

    @staticmethod
    def _child_identity(pid: int) -> Any:
        inspector = SystemProcessInspector()
        current = inspector.current_identity()
        probe = inspector.probe(current.host, pid)
        if probe.status is not ProcessStatus.ALIVE or probe.identity is None:
            raise GeospatialWorkerError("Could not establish Worker process identity")
        return probe.identity


def _operation(command_type: str) -> str:
    operations = {
        GEOSPATIAL_PREPARE_REGIONS_COMMAND: "prepare_regions",
        GEOSPATIAL_SAMPLE_ELEVATION_COMMAND: "sample_elevation",
    }
    try:
        return operations[command_type]
    except KeyError as error:
        raise GeospatialWorkerError(
            f"Unsupported geospatial command type: {command_type}"
        ) from error


def _required_text(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise GeospatialWorkerError(f"{field} is required")
    return value.strip()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GeospatialWorkerError("Worker did not produce a valid result") from error
    if not isinstance(value, dict):
        raise GeospatialWorkerError("Worker result must be an object")
    return value


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False),
        encoding="utf-8",
    )


def _tail(path: Path, limit: int = 12000) -> str:
    try:
        content = path.read_bytes()
    except OSError:
        return ""
    return content[-limit:].decode("utf-8", errors="replace")


def _minimal_environment(workdir: Path) -> dict[str, str]:
    allowed = ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT")
    environment = {key: os.environ[key] for key in allowed if key in os.environ}
    environment.update(
        {
            "PYTHONUTF8": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "TEMP": str(workdir),
            "TMP": str(workdir),
        }
    )
    return environment


def _now_text() -> str:
    return datetime.now(UTC).isoformat()
