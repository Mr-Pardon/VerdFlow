"""Supervise one Blender Job attempt through a private file protocol."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from verdflow.contracts import (
    ELEVATION_SAMPLING_COMMAND_SCHEMA,
    ELEVATION_SAMPLING_COMMAND_VERSION,
    JobErrorInfo,
    JobInfo,
    JobStatus,
    PointSetKind,
    REGION_EXTRACTION_COMMAND_SCHEMA,
    REGION_EXTRACTION_COMMAND_VERSION,
    SourceKind,
    WorkerProcessInfo,
    WorkerProcessStatus,
)
from verdflow.core import JobRuntime
from verdflow.project_store import (
    ProcessStatus,
    SystemProcessInspector,
)
from verdflow.worker_runtime import ProcessTreeGuard, terminate_process_by_pid

from .algorithms import validate_rule_pack
from .asset_libraries import BlenderAssetLibraryRegistry
from .protocol import (
    BLENDER_EXTRACT_REGIONS_COMMAND,
    BLENDER_INSPECT_ASSET_LIBRARY_COMMAND,
    BLENDER_INSPECT_SCENE_COMMAND,
    BLENDER_PROBE_COMMAND,
    BLENDER_SAMPLE_ELEVATION_COMMAND,
    BLENDER_SUPPORTED_COMMANDS,
    BLENDER_WORKER_ADAPTER_ID,
    CAPABILITY_BUNDLE_VERSION,
    WORKER_PROTOCOL_VERSION,
    BlenderWorkerConfig,
    BlenderWorkerError,
)


BLOCKING_WORKER_STAGES = {
    "importing_fbx",
    "loading_scene_cache",
    "saving_scene_cache",
    "building_elevation_bvh",
    "opening_asset_library",
}


class BlenderWorkerSupervisor:
    """Own Blender process lifetime while Job state remains in Core."""

    def __init__(
        self,
        runtime: JobRuntime,
        config: BlenderWorkerConfig,
        *,
        worker_id: str | None = None,
        command_factory: Callable[[Path, Path], list[str]] | None = None,
        process_started: Callable[[WorkerProcessInfo], None] | None = None,
        asset_library_registry: BlenderAssetLibraryRegistry | None = None,
    ) -> None:
        config.validate()
        self.runtime = runtime
        self.config = config
        self.worker_id = worker_id or f"blender_{uuid.uuid4().hex}"
        self._command_factory = command_factory
        self._process_started = process_started
        self._asset_library_registry = asset_library_registry
        self._cancel_requests: set[str] = set()
        self._cancel_lock = threading.Lock()
        self.config.runtime_root.mkdir(parents=True, exist_ok=True)
        if self.config.scene_cache_root is not None:
            self.config.scene_cache_root.mkdir(parents=True, exist_ok=True)

    def request_cancel(self, job_id: str) -> None:
        """Thread-safe signal consumed by the supervisor's owning thread."""
        with self._cancel_lock:
            self._cancel_requests.add(job_id)

    def run_once(self) -> JobInfo | None:
        claimed = self.runtime.claim_next(
            self.worker_id,
            command_types=BLENDER_SUPPORTED_COMMANDS,
        )
        if claimed is None:
            return None
        return self._run_claimed(claimed)

    def reconcile_recorded_processes(self) -> int:
        """Converge process records left active by a previous Core instance."""
        inspector = SystemProcessInspector()
        recovered = 0
        for record in self.runtime.store.list_active_worker_processes():
            if record.adapter_id != BLENDER_WORKER_ADAPTER_ID:
                continue
            probe = inspector.probe(record.host, record.pid)
            if probe.status is ProcessStatus.UNKNOWN:
                raise BlenderWorkerError(
                    "Recorded worker process identity cannot be inspected: "
                    f"{record.job_id}:{record.attempt_number}"
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
            self._remove_attempt_control_dirs(
                record.job_id,
                record.attempt_number,
            )
            recovered += 1
        return recovered

    def _run_claimed(self, claimed: Any) -> JobInfo:
        lease = claimed.lease
        control_dir = self._create_control_dir(
            lease.job_id,
            lease.attempt_count,
        )
        command_path = control_dir / "command.json"
        stdout_path = control_dir / "stdout.log"
        stderr_path = control_dir / "stderr.log"
        process: subprocess.Popen[bytes] | None = None
        guard = ProcessTreeGuard()
        registered = False
        final_process_status = WorkerProcessStatus.EXITED
        termination: tuple[str, str, bool] | None = None
        cancelled = False
        job_progress = float(claimed.job.progress)

        stdout_stream = stdout_path.open("wb")
        stderr_stream = stderr_path.open("wb")
        try:
            self.runtime.heartbeat(
                lease,
                progress=job_progress,
                stage="preparing_inputs",
            )
            command = self._prepare_command(
                self._load_command(
                    claimed.job.command.artifact_id,
                    claimed.job.command.command_type,
                ),
                claimed.job.command.command_type,
                control_dir,
                lease=lease,
            )
            job_progress = max(job_progress, 0.02)
            self.runtime.heartbeat(
                lease,
                progress=job_progress,
                stage="launching_blender",
            )
            _write_json(command_path, command)
            process = subprocess.Popen(
                self._command(control_dir, command_path),
                stdin=subprocess.DEVNULL,
                stdout=stdout_stream,
                stderr=stderr_stream,
                env=_blender_environment(),
                **guard.popen_options(),
            )
            try:
                guard.attach(process)
            except Exception:
                process.kill()
                process.wait(timeout=self.config.termination_grace_seconds)
                raise
            self.runtime.heartbeat(lease)

            identity = self._child_identity(process.pid)
            process_record = WorkerProcessInfo(
                job_id=lease.job_id,
                attempt_number=lease.attempt_count,
                adapter_id=BLENDER_WORKER_ADAPTER_ID,
                host=identity.host,
                pid=identity.pid,
                started_at_token=identity.started_at_token,
                status=WorkerProcessStatus.RUNNING,
                started_at=_now_text(),
                heartbeat_at=None,
                finished_at=None,
                exit_code=None,
            )
            self.runtime.store.register_worker_process(process_record)
            registered = True
            self.runtime.heartbeat(lease)
            if self._process_started is not None:
                self._process_started(process_record)
            _write_json(
                control_dir / "launch.json",
                {
                    "schema": "verdflow.blender-worker.launch",
                    "version": WORKER_PROTOCOL_VERSION,
                    "job_id": lease.job_id,
                    "attempt_number": lease.attempt_count,
                    "pid": identity.pid,
                    "host": identity.host,
                    "started_at_token": identity.started_at_token,
                    "containment_mode": guard.mode,
                },
            )

            launched_at = time.monotonic()
            ready_at: float | None = None
            last_heartbeat_at = launched_at
            heartbeat_sequence = -1
            progress_sequence = 0
            last_worker_stage = (
                "probing_blender"
                if command.get("operation") == "probe"
                else "importing_fbx"
            )
            last_lease_refresh_at = launched_at
            lease_refresh_interval = max(
                0.05,
                min(
                    self.runtime.lease_duration.total_seconds() / 3.0,
                    1.0,
                ),
            )
            while process.poll() is None:
                now = time.monotonic()
                if self._take_cancel_request(lease.job_id):
                    if self.runtime.get(lease.job_id).status is JobStatus.RUNNING:
                        self.runtime.request_cancel(lease.job_id)
                    (control_dir / "cancel.request").touch()
                    cancelled = True
                    final_process_status = WorkerProcessStatus.TERMINATED
                    break

                ready_path = control_dir / "ready.json"
                if ready_at is None and ready_path.is_file():
                    ready = _read_protocol_json(
                        ready_path,
                        "verdflow.blender-worker.ready",
                    )
                    if int(ready.get("pid", -1)) != process.pid:
                        raise BlenderWorkerError(
                            "Blender ready message has a different PID"
                        )
                    _require_ready_capability(
                        ready,
                        _required_capability(
                            claimed.job.command.command_type
                        ),
                    )
                    ready_at = now
                    last_heartbeat_at = now

                progress_dir = control_dir / "progress-events"
                if ready_at is not None and progress_dir.is_dir():
                    while True:
                        next_progress = progress_dir / f"{progress_sequence + 1:08d}.json"
                        if not next_progress.is_file():
                            break
                        update = _read_protocol_json(
                            next_progress,
                            "verdflow.blender-worker.progress",
                        )
                        sequence = int(update.get("sequence", -1))
                        if sequence != progress_sequence + 1:
                            raise BlenderWorkerError(
                                "Blender progress sequence is not contiguous"
                            )
                        next_value = float(update["progress"])
                        if next_value < job_progress:
                            raise BlenderWorkerError(
                                "Blender progress moved backwards"
                            )
                        last_worker_stage = str(update["stage"])
                        self.runtime.heartbeat(
                            lease,
                            progress=next_value,
                            stage=last_worker_stage,
                        )
                        job_progress = next_value
                        progress_sequence = sequence
                        last_lease_refresh_at = now

                heartbeat_path = control_dir / "heartbeat.json"
                if ready_at is not None and heartbeat_path.is_file():
                    heartbeat = _read_protocol_json(
                        heartbeat_path,
                        "verdflow.blender-worker.heartbeat",
                    )
                    sequence = int(heartbeat.get("sequence", -1))
                    if sequence > heartbeat_sequence:
                        heartbeat_sequence = sequence
                        last_heartbeat_at = now
                        last_worker_stage = str(heartbeat["stage"])
                        heartbeat_time = _now_text()
                        self.runtime.store.heartbeat_worker_process(
                            lease.job_id,
                            lease.attempt_count,
                            heartbeat_time,
                        )
                        if progress_dir.is_dir():
                            self.runtime.heartbeat(lease)
                        else:
                            self.runtime.heartbeat(
                                lease,
                                progress=max(
                                    job_progress,
                                    float(heartbeat["progress"]),
                                ),
                                stage=str(heartbeat["stage"]),
                            )
                            job_progress = max(
                                job_progress,
                                float(heartbeat["progress"]),
                            )
                        last_lease_refresh_at = now

                if now - last_lease_refresh_at >= lease_refresh_interval:
                    self.runtime.heartbeat(lease)
                    last_lease_refresh_at = now

                if (
                    ready_at is None
                    and now - launched_at
                    > self.config.ready_timeout_seconds
                ):
                    termination = (
                        "BLENDER_READY_TIMEOUT",
                        "Blender did not publish ready before the deadline",
                        True,
                    )
                    final_process_status = WorkerProcessStatus.TERMINATED
                    break
                if (
                    ready_at is not None
                    and now - last_heartbeat_at
                    > (
                        self.config.blocking_operation_timeout_seconds
                        if last_worker_stage in BLOCKING_WORKER_STAGES
                        else self.config.heartbeat_timeout_seconds
                    )
                ):
                    termination = (
                        "BLENDER_HEARTBEAT_TIMEOUT",
                        "Blender worker heartbeat stopped",
                        True,
                    )
                    final_process_status = WorkerProcessStatus.TERMINATED
                    break
                if (
                    now - launched_at
                    > self.config.execution_timeout_seconds
                ):
                    termination = (
                        "BLENDER_EXECUTION_TIMEOUT",
                        "Blender worker exceeded the execution deadline",
                        True,
                    )
                    final_process_status = WorkerProcessStatus.TERMINATED
                    break
                time.sleep(self.config.poll_interval_seconds)

            if process.poll() is None:
                guard.terminate(
                    process,
                    grace_seconds=self.config.termination_grace_seconds,
                )
            else:
                process.wait()
        except Exception as error:
            termination = (
                "BLENDER_SUPERVISOR_ERROR",
                str(error),
                True,
            )
            final_process_status = WorkerProcessStatus.TERMINATED
            if process is not None and process.poll() is None:
                guard.terminate(
                    process,
                    grace_seconds=self.config.termination_grace_seconds,
                )
        finally:
            stdout_stream.close()
            stderr_stream.close()
            guard.close()

        exit_code = process.returncode if process is not None else None
        try:
            if registered:
                self.runtime.store.finish_worker_process(
                    lease.job_id,
                    lease.attempt_count,
                    status=final_process_status,
                    finished_at=_now_text(),
                    exit_code=exit_code,
                )
            if cancelled:
                return self.runtime.acknowledge_cancel(lease)
            if termination is not None:
                code, message, retryable = termination
                return self.runtime.fail(
                    lease,
                    JobErrorInfo(
                        code=code,
                        message=message,
                        retryable=retryable,
                        diagnostic_artifact_id=self._diagnostic_artifact(
                            control_dir,
                            code,
                            message,
                            exit_code,
                        ),
                    ),
                )
            result_path = control_dir / "result.json"
            if exit_code != 0 or not result_path.is_file():
                message = (
                    f"Blender exited with code {exit_code} "
                    "without a valid result"
                )
                return self.runtime.fail(
                    lease,
                    JobErrorInfo(
                        code="BLENDER_PROCESS_FAILED",
                        message=message,
                        retryable=True,
                        diagnostic_artifact_id=self._diagnostic_artifact(
                            control_dir,
                            "BLENDER_PROCESS_FAILED",
                            message,
                            exit_code,
                        ),
                    ),
                )
            result = _read_protocol_json(
                result_path,
                "verdflow.blender-worker.result",
            )
            if result.get("status") != "succeeded":
                message = str(result.get("message", "Blender worker failed"))
                return self.runtime.fail(
                    lease,
                    JobErrorInfo(
                        code="BLENDER_OPERATION_FAILED",
                        message=message,
                        retryable=False,
                        diagnostic_artifact_id=self._diagnostic_artifact(
                            control_dir,
                            "BLENDER_OPERATION_FAILED",
                            message,
                            exit_code,
                        ),
                    ),
                )
            expected_operation = _operation_for_command_type(
                claimed.job.command.command_type
            )
            if result.get("operation") != expected_operation:
                raise BlenderWorkerError(
                    "Blender result operation does not match the Job command"
                )
            if expected_operation == "inspect_scene":
                self._publish_scene_cache(
                    control_dir,
                    result,
                    inspection_job_id=lease.job_id,
                )
            result_artifact_id = self._register_result(
                control_dir,
                result,
                job_id=lease.job_id,
                attempt_number=lease.attempt_count,
            )
            return self.runtime.succeed(
                lease,
                result_artifact_id=result_artifact_id,
            )
        except Exception as error:
            current = self.runtime.get(lease.job_id)
            if current.status is not JobStatus.RUNNING:
                raise
            message = str(error) or type(error).__name__
            return self.runtime.fail(
                lease,
                JobErrorInfo(
                    code="BLENDER_RESULT_INVALID",
                    message=message,
                    retryable=False,
                    diagnostic_artifact_id=self._diagnostic_artifact(
                        control_dir,
                        "BLENDER_RESULT_INVALID",
                        message,
                        exit_code,
                    ),
                ),
            )
        finally:
            shutil.rmtree(control_dir, ignore_errors=True)

    def _load_command(
        self,
        artifact_id: str,
        command_type: str,
    ) -> dict[str, Any]:
        with self.runtime.store.open_artifact(artifact_id) as stream:
            try:
                command = json.load(stream)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise BlenderWorkerError(
                    "Blender command artifact is not valid UTF-8 JSON"
                ) from error
        if not isinstance(command, dict):
            raise BlenderWorkerError("Blender command must be a JSON object")
        if command_type == BLENDER_SAMPLE_ELEVATION_COMMAND:
            expected_schema = ELEVATION_SAMPLING_COMMAND_SCHEMA
            expected_version = ELEVATION_SAMPLING_COMMAND_VERSION
        elif (
            command_type == BLENDER_EXTRACT_REGIONS_COMMAND
            and command.get("schema") == REGION_EXTRACTION_COMMAND_SCHEMA
        ):
            expected_schema = REGION_EXTRACTION_COMMAND_SCHEMA
            expected_version = REGION_EXTRACTION_COMMAND_VERSION
        else:
            expected_schema = "verdflow.blender-worker.command"
            expected_version = WORKER_PROTOCOL_VERSION
        if command.get("schema") != expected_schema:
            raise BlenderWorkerError("Blender command schema is unsupported")
        if str(command.get("version")) != expected_version:
            raise BlenderWorkerError("Blender command version is unsupported")
        return command

    def _prepare_command(
        self,
        command: dict[str, Any],
        command_type: str,
        control_dir: Path,
        *,
        lease: Any | None = None,
    ) -> dict[str, Any]:
        expected_operation = _operation_for_command_type(command_type)
        if command.get("operation") != expected_operation:
            raise BlenderWorkerError(
                "Blender command operation does not match its command type"
            )
        if any(
            field in command
            for field in (
                "source_path",
                "source_members",
                "scene_cache_path",
                "rule_pack_path",
                "points_path",
                "library_path",
            )
        ):
            raise BlenderWorkerError(
                "Durable Blender commands cannot contain local input paths"
            )
        prepared = dict(command)
        if expected_operation == "probe":
            return prepared

        if expected_operation == "inspect_asset_library":
            if "expected_content_hash" in command:
                raise BlenderWorkerError(
                    "expected_content_hash is not supported; refresh the Catalog "
                    "or use a future managed snapshot provider policy"
                )
            if self._asset_library_registry is None:
                raise BlenderWorkerError(
                    "Blender asset library registry is not configured"
                )
            library_id = _required_text(command, "library_id")
            required_names = command.get("required_collection_names", [])
            if (
                not isinstance(required_names, list)
                or not all(
                    isinstance(name, str) and name.strip()
                    for name in required_names
                )
            ):
                raise BlenderWorkerError(
                    "required_collection_names must be an array of non-empty strings"
                )
            registration = self._asset_library_registry.resolve(library_id)
            prepared["library_path"] = str(registration.library_path)
            prepared["required_collection_names"] = required_names
            return prepared

        source_id = _required_text(command, "source_id")
        source_artifact_id = _required_text(
            command,
            "source_artifact_id",
        )
        source = self.runtime.store.get_source(source_id)
        if source.artifact_id != source_artifact_id:
            raise BlenderWorkerError(
                "source_artifact_id does not belong to source_id"
            )
        inputs = control_dir / "inputs"
        if source.kind is SourceKind.ASSEMBLY:
            prepared["source_members"] = self._stage_source_members(
                source.source_id,
                inputs,
                lease=lease,
            )
        elif source.kind is SourceKind.FBX:
            source_path = inputs / "source.fbx"
            if lease is not None:
                self.runtime.heartbeat(lease)
            self.runtime.store.materialize_artifact(
                source_artifact_id,
                source_path,
            )
            if lease is not None:
                self.runtime.heartbeat(lease)
            prepared["source_path"] = str(source_path)
        else:
            raise BlenderWorkerError(
                "This Blender capability accepts FBX or Assembly Sources only"
            )
        prepared["source_name"] = source.original_name
        option_source = command
        if expected_operation == "sample_elevation":
            options = command.get("options", {})
            if not isinstance(options, dict):
                raise BlenderWorkerError("options must be an object")
            option_source = options
            provider = command.get("provider")
            if not isinstance(provider, dict):
                raise BlenderWorkerError("Elevation provider is required")
            if provider.get("id") != BLENDER_WORKER_ADAPTER_ID:
                raise BlenderWorkerError(
                    "Elevation command targets another provider"
                )
            if str(provider.get("version")) != WORKER_PROTOCOL_VERSION:
                raise BlenderWorkerError(
                    "Elevation provider version is unsupported"
                )
            prepared.pop("options", None)
        elif (
            expected_operation == "extract_regions"
            and command.get("schema") == REGION_EXTRACTION_COMMAND_SCHEMA
        ):
            provider = command.get("provider")
            if not isinstance(provider, dict):
                raise BlenderWorkerError("Region extraction provider is required")
            if provider.get("id") != BLENDER_WORKER_ADAPTER_ID:
                raise BlenderWorkerError(
                    "Region extraction command targets another provider"
                )
            if str(provider.get("version")) != WORKER_PROTOCOL_VERSION:
                raise BlenderWorkerError(
                    "Region extraction provider version is unsupported"
                )

        import_settings = option_source.get("import_settings", {})
        if not isinstance(import_settings, dict):
            raise BlenderWorkerError("import_settings must be an object")
        prepared["import_settings"] = import_settings

        if expected_operation in {"extract_regions", "sample_elevation"}:
            scene_revision_id = _required_text(
                command,
                "scene_revision_id",
            )
            scene_revision = self.runtime.store.get_scene_revision(
                scene_revision_id
            )
            if scene_revision.source_id != source.source_id:
                raise BlenderWorkerError(
                    "scene_revision_id does not belong to source_id"
                )
            scene_cache_path = self._scene_cache_for_revision(scene_revision)
            if scene_cache_path is not None:
                prepared["scene_cache_path"] = str(scene_cache_path)
            if command.get("schema") in {
                ELEVATION_SAMPLING_COMMAND_SCHEMA,
                REGION_EXTRACTION_COMMAND_SCHEMA,
            }:
                coordinate_frame_id = _required_text(
                    command,
                    "coordinate_frame_id",
                )
                if scene_revision.coordinate_frame_id != coordinate_frame_id:
                    raise BlenderWorkerError(
                        "Command CoordinateFrame differs from SceneRevision"
                    )

        if expected_operation == "extract_regions":
            rule_pack_artifact_id = _required_text(
                command,
                "rule_pack_artifact_id",
            )
            rule_pack_path = inputs / "rule_pack.json"
            self.runtime.store.materialize_artifact(
                rule_pack_artifact_id,
                rule_pack_path,
            )
            try:
                rule_pack = json.loads(
                    rule_pack_path.read_text(encoding="utf-8")
                )
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise BlenderWorkerError(
                    "Rule pack Artifact is not valid UTF-8 JSON"
                ) from error
            if not isinstance(rule_pack, dict):
                raise BlenderWorkerError("Rule pack must be a JSON object")
            try:
                validate_rule_pack(
                    rule_pack,
                    allow_contains=bool(command.get("allow_contains", False)),
                )
            except ValueError as error:
                raise BlenderWorkerError(str(error)) from error
            prepared["rule_pack_path"] = str(rule_pack_path)

        if expected_operation == "sample_elevation":
            parent_point_set_id = _required_text(
                command,
                "parent_point_set_id",
            )
            parent = self.runtime.store.get_point_set(
                parent_point_set_id
            )
            run = self.runtime.store.get_planting_run(
                parent.planting_run_id
            )
            if run.scene_revision_id != scene_revision_id:
                raise BlenderWorkerError(
                    "parent_point_set_id differs from SceneRevision"
                )
            points_artifact_id = _required_text(
                command,
                "points_artifact_id",
            )
            if parent.point_artifact_id != points_artifact_id:
                raise BlenderWorkerError(
                    "points_artifact_id does not belong to parent PointSet"
                )
            if parent.kind is not PointSetKind.GENERATED:
                raise BlenderWorkerError(
                    "Elevation input must be a generated PointSet"
                )
            points_artifact = self.runtime.store.get_artifact(
                points_artifact_id
            )
            if (
                points_artifact.schema_id != "verdflow.points"
                or points_artifact.schema_version != "2"
            ):
                raise BlenderWorkerError(
                    "Elevation input Artifact must be verdflow.points v2"
                )
            points_path = inputs / "points.json"
            self.runtime.store.materialize_artifact(
                points_artifact_id,
                points_path,
            )
            prepared["points_path"] = str(points_path)
            if source.kind is SourceKind.ASSEMBLY:
                regions = self.runtime.store.list_regions(
                    run.region_set_id
                )
                region_source_members = {
                    region.region_id: region.source_member_id
                    for region in regions
                    if region.source_member_id is not None
                }
                if len(region_source_members) != len(regions):
                    raise BlenderWorkerError(
                        "Assembly elevation requires Region member provenance"
                    )
                prepared["region_source_member_ids"] = (
                    region_source_members
                )
            target_names = option_source.get("target_object_names", [])
            if (
                not isinstance(target_names, list)
                or not all(isinstance(name, str) for name in target_names)
            ):
                raise BlenderWorkerError(
                    "target_object_names must be an array of strings"
                )
            prepared["target_object_names"] = target_names
            for field in (
                "direction",
                "ray_start_offset",
                "ray_distance",
                "include_hidden",
            ):
                if field in option_source:
                    prepared[field] = option_source[field]
        return prepared

    def _scene_cache_for_revision(self, scene_revision: Any) -> Path | None:
        if self.config.scene_cache_root is None:
            return None
        if not scene_revision.importer_version.startswith(
            f"{CAPABILITY_BUNDLE_VERSION}+"
        ):
            return None
        with self.runtime.store.open_artifact(
            scene_revision.scene_manifest_artifact_id
        ) as stream:
            try:
                manifest = json.load(stream)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise BlenderWorkerError(
                    "SceneRevision manifest is not valid UTF-8 JSON"
                ) from error
        if not isinstance(manifest, dict):
            raise BlenderWorkerError("SceneRevision manifest must be an object")
        producer_job_id = _required_text(manifest, "producer_job_id")
        candidate = self.config.scene_cache_root / f"{producer_job_id}.blend"
        return candidate if candidate.is_file() else None

    def _publish_scene_cache(
        self,
        control_dir: Path,
        result: dict[str, Any],
        *,
        inspection_job_id: str,
    ) -> None:
        relative = result.pop("scene_cache_file", None)
        if relative is None or self.config.scene_cache_root is None:
            return
        if not isinstance(relative, str) or not relative.strip():
            raise BlenderWorkerError("Scene cache file path is invalid")
        root = control_dir.resolve()
        source = (control_dir / relative).resolve()
        if not source.is_relative_to(root):
            raise BlenderWorkerError("Scene cache escaped the private control directory")
        if not source.is_file():
            raise BlenderWorkerError("Scene cache file is missing")
        destination = self.config.scene_cache_root / f"{inspection_job_id}.blend"
        temporary = destination.with_name(
            f".{destination.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            shutil.move(str(source), str(temporary))
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    def _stage_source_members(
        self,
        assembly_source_id: str,
        inputs: Path,
        *,
        lease: Any | None = None,
    ) -> list[dict[str, object]]:
        staged: list[dict[str, object]] = []
        for member in self.runtime.store.list_source_assembly_members(
            assembly_source_id
        ):
            source = self.runtime.store.get_source(member.source_id)
            if source.kind is not SourceKind.FBX:
                raise BlenderWorkerError(
                    "Blender Source Assemblies currently accept FBX members only"
                )
            source_path = (
                inputs
                / "sources"
                / f"{member.ordinal:04d}.fbx"
            )
            if lease is not None:
                self.runtime.heartbeat(lease)
            self.runtime.store.materialize_artifact(
                source.artifact_id,
                source_path,
            )
            if lease is not None:
                self.runtime.heartbeat(lease)
            staged.append(
                {
                    "source_member_id": member.source_member_id,
                    "source_id": member.source_id,
                    "source_name": source.original_name,
                    "source_path": str(source_path),
                    "source_to_scene_matrix_row_major": list(
                        member.source_to_scene_matrix_row_major
                    ),
                }
            )
        if not staged:
            raise BlenderWorkerError("Source Assembly has no members")
        return staged

    def _register_result(
        self,
        control_dir: Path,
        result: dict[str, Any],
        *,
        job_id: str,
        attempt_number: int,
    ) -> str:
        descriptors = result.get("output_files", [])
        if not isinstance(descriptors, list):
            raise BlenderWorkerError("Worker output_files must be a list")
        registered: list[dict[str, Any]] = []
        roles: set[str] = set()
        root = control_dir.resolve()
        for descriptor in descriptors:
            if not isinstance(descriptor, dict):
                raise BlenderWorkerError(
                    "Worker output descriptor must be an object"
                )
            role = _required_text(descriptor, "role")
            if role in roles:
                raise BlenderWorkerError(
                    f"Worker output role is duplicated: {role}"
                )
            roles.add(role)
            relative_path = _required_text(
                descriptor,
                "relative_path",
            )
            output_path = (control_dir / relative_path).resolve()
            if not output_path.is_relative_to(root):
                raise BlenderWorkerError(
                    "Worker output path escaped the private control directory"
                )
            if not output_path.is_file():
                raise BlenderWorkerError(
                    f"Worker output file is missing: {role}"
                )
            media_type = _required_text(descriptor, "media_type")
            schema_id = _required_text(descriptor, "schema_id")
            schema_version = _required_text(
                descriptor,
                "schema_version",
            )
            artifact = self.runtime.store.put_file(
                output_path,
                media_type=media_type,
                schema_id=schema_id,
                schema_version=schema_version,
            )
            registered.append(
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

        normalized = dict(result)
        normalized.pop("output_files", None)
        normalized["job_id"] = job_id
        normalized["attempt_number"] = attempt_number
        normalized["result_kind"] = _result_kind(
            str(result.get("operation", ""))
        )
        normalized["producer"] = _producer_provenance(result)
        normalized["output_artifacts"] = registered
        normalized_path = control_dir / "registered-result.json"
        _write_json(normalized_path, normalized)
        artifact = self.runtime.store.put_file(
            normalized_path,
            media_type=(
                "application/vnd.verdflow.blender-worker-result+json"
            ),
            schema_id="verdflow.blender-worker.result",
            schema_version=WORKER_PROTOCOL_VERSION,
        )
        return artifact.artifact_id

    def _create_control_dir(
        self,
        job_id: str,
        attempt_number: int,
    ) -> Path:
        path = self.config.runtime_root / (
            _control_dir_prefix(job_id, attempt_number)
            + uuid.uuid4().hex[:6]
        )
        path.mkdir(parents=False, exist_ok=False)
        return path

    def _command(
        self,
        control_dir: Path,
        command_path: Path,
    ) -> list[str]:
        if self._command_factory is not None:
            return self._command_factory(control_dir, command_path)
        worker_entry = Path(__file__).with_name("worker_entry.py")
        return [
            str(self.config.blender_executable),
            "--factory-startup",
            "--background",
            "--python",
            str(worker_entry),
            "--",
            "--control-dir",
            str(control_dir),
            "--command",
            str(command_path),
        ]

    @staticmethod
    def _child_identity(pid: int) -> Any:
        inspector = SystemProcessInspector()
        current = inspector.current_identity()
        probe = inspector.probe(current.host, pid)
        if (
            probe.status is not ProcessStatus.ALIVE
            or probe.identity is None
        ):
            raise BlenderWorkerError(
                "Could not establish a stable Blender process identity"
            )
        return probe.identity

    def _take_cancel_request(self, job_id: str) -> bool:
        with self._cancel_lock:
            if job_id not in self._cancel_requests:
                return False
            self._cancel_requests.remove(job_id)
            return True

    def _remove_attempt_control_dirs(
        self,
        job_id: str,
        attempt_number: int,
    ) -> None:
        prefix = _control_dir_prefix(job_id, attempt_number)
        for path in self.config.runtime_root.iterdir():
            if path.is_dir() and path.name.startswith(prefix):
                shutil.rmtree(path, ignore_errors=True)

    def _diagnostic_artifact(
        self,
        control_dir: Path,
        code: str,
        message: str,
        exit_code: int | None,
    ) -> str:
        payload = {
            "schema": "verdflow.blender-worker.diagnostic",
            "version": WORKER_PROTOCOL_VERSION,
            "code": code,
            "message": message,
            "exit_code": exit_code,
            "stdout_tail": _tail(control_dir / "stdout.log"),
            "stderr_tail": _tail(control_dir / "stderr.log"),
        }
        result_path = control_dir / "result.json"
        if result_path.is_file():
            try:
                payload["worker_result"] = json.loads(
                    result_path.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                pass
        artifact = self.runtime.store.put_bytes(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8"),
            media_type=(
                "application/vnd.verdflow.blender-worker-diagnostic+json"
            ),
            schema_id="verdflow.blender-worker.diagnostic",
            schema_version=WORKER_PROTOCOL_VERSION,
        )
        return artifact.artifact_id


def _blender_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for key in ("PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE"):
        environment.pop(key, None)
    environment["PYTHONUTF8"] = "1"
    return environment


def _control_dir_prefix(job_id: str, attempt_number: int) -> str:
    digest = hashlib.sha256(job_id.encode("utf-8")).hexdigest()[:12]
    return f"a{attempt_number}-{digest}-"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _read_protocol_json(path: Path, schema: str) -> dict[str, Any]:
    last_error: OSError | json.JSONDecodeError | None = None
    payload: object = None
    for attempt in range(3):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            break
        except (OSError, json.JSONDecodeError) as error:
            last_error = error
            if attempt < 2:
                time.sleep(0.01)
    else:
        raise BlenderWorkerError(
            f"Worker protocol file is invalid: {path.name}"
        ) from last_error
    if not isinstance(payload, dict):
        raise BlenderWorkerError(
            f"Worker protocol payload is not an object: {path.name}"
        )
    if payload.get("schema") != schema:
        raise BlenderWorkerError(
            f"Worker protocol schema is invalid: {path.name}"
        )
    if str(payload.get("version")) != WORKER_PROTOCOL_VERSION:
        raise BlenderWorkerError(
            f"Worker protocol version is invalid: {path.name}"
        )
    return payload


def _operation_for_command_type(command_type: str) -> str:
    operations = {
        BLENDER_PROBE_COMMAND: "probe",
        BLENDER_INSPECT_ASSET_LIBRARY_COMMAND: "inspect_asset_library",
        BLENDER_INSPECT_SCENE_COMMAND: "inspect_scene",
        BLENDER_EXTRACT_REGIONS_COMMAND: "extract_regions",
        BLENDER_SAMPLE_ELEVATION_COMMAND: "sample_elevation",
    }
    try:
        return operations[command_type]
    except KeyError as error:
        raise BlenderWorkerError(
            f"Unsupported Blender command type: {command_type}"
        ) from error


def _required_capability(command_type: str) -> str | None:
    capabilities = {
        BLENDER_PROBE_COMMAND: None,
        BLENDER_INSPECT_ASSET_LIBRARY_COMMAND: "assets.inspect-library",
        BLENDER_INSPECT_SCENE_COMMAND: "scene.inspect",
        BLENDER_EXTRACT_REGIONS_COMMAND: "regions.extract",
        BLENDER_SAMPLE_ELEVATION_COMMAND: "points.sample-elevation",
    }
    try:
        return capabilities[command_type]
    except KeyError as error:
        raise BlenderWorkerError(
            f"Unsupported Blender command type: {command_type}"
        ) from error


def _result_kind(operation: str) -> str:
    kinds = {
        "probe": "worker_probe",
        "inspect_asset_library": "asset_library_inspection",
        "inspect_scene": "scene_inspection",
        "extract_regions": "region_extraction",
        "sample_elevation": "elevation_sampling",
    }
    try:
        return kinds[operation]
    except KeyError as error:
        raise BlenderWorkerError(
            f"Unsupported Blender result operation: {operation}"
        ) from error


def _producer_provenance(
    result: dict[str, Any],
) -> dict[str, Any]:
    producer: dict[str, Any] = {
        "id": BLENDER_WORKER_ADAPTER_ID,
        "version": WORKER_PROTOCOL_VERSION,
    }
    capability = result.get("capability_bundle")
    if isinstance(capability, dict):
        producer["capability_bundle"] = capability
    blender = result.get("blender")
    if isinstance(blender, dict):
        producer["runtime"] = {
            "id": "blender",
            **blender,
        }
    bundle_version = (
        capability.get("bundle_version")
        if isinstance(capability, dict)
        else None
    )
    runtime_version = (
        blender.get("version")
        if isinstance(blender, dict)
        else None
    )
    if bundle_version and runtime_version:
        producer["version"] = (
            f"{bundle_version}+blender-{runtime_version}"
        )
    return producer


def _require_ready_capability(
    ready: dict[str, Any],
    capability_id: str | None,
) -> None:
    if capability_id is None:
        return
    bundle = ready.get("capability_bundle")
    if not isinstance(bundle, dict):
        raise BlenderWorkerError(
            "Blender Worker did not declare its capability bundle"
        )
    capabilities = bundle.get("capabilities")
    if not isinstance(capabilities, list):
        raise BlenderWorkerError(
            "Blender Worker capability manifest is invalid"
        )
    identifiers = {
        str(item.get("id"))
        for item in capabilities
        if isinstance(item, dict)
    }
    if capability_id not in identifiers:
        raise BlenderWorkerError(
            f"Blender Worker does not provide capability: {capability_id}"
        )


def _required_text(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise BlenderWorkerError(f"{field} is required")
    return value.strip()


def _tail(path: Path, limit: int = 12000) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    return data[-limit:].decode("utf-8", errors="replace")


def _now_text() -> str:
    return datetime.now(UTC).isoformat()
