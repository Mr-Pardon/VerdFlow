"""Supervise geospatial Jobs through an isolated Python subprocess."""

from __future__ import annotations

import errno
from functools import partial
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
from datetime import UTC, datetime
from typing import Any, Callable, Generator

from verdflow.contracts import (
    ELEVATION_SAMPLING_COMMAND_SCHEMA,
    ELEVATION_SAMPLING_COMMAND_VERSION,
    JobErrorInfo,
    JobInfo,
    JobLease,
    JobStatus,
    PointSetKind,
    REGION_SOURCE_AUTO_PUBLISH_VERSION,
    REGION_SOURCE_COMMAND_SCHEMA,
    REGION_SOURCE_COMMAND_VERSION,
    SourceKind,
    WorkerProcessInfo,
    WorkerProcessStatus,
)
from verdflow.core import JobRuntime, RegionSourceImportError, WorkerDescriptor
from verdflow.core.workers import WorkerJobPoller
from verdflow.project_store import JobLeaseConflictError, ProcessStatus, SystemProcessInspector
from verdflow.worker_runtime import ProcessTreeGuard, terminate_process_by_pid
from .errors import GeospatialDataError, is_host_memory_error
from .imagery import MIN_FREE_DISK_BYTES
from .imagery_memory import ImageryMemoryPool, ImageryMemoryReservation

from .protocol import (
    GEOSPATIAL_ADAPTER_ID,
    GEOSPATIAL_PREPARE_REGIONS_COMMAND,
    GEOSPATIAL_PROTOCOL_VERSION,
    GEOSPATIAL_SAMPLE_ELEVATION_COMMAND,
    GEOSPATIAL_SUPPORTED_COMMANDS,
    IMAGERY_PREPARE_REGIONS_COMMAND,
    IMAGERY_REGION_PROVIDER_ID,
    GeospatialWorkerConfig,
    ImageryRegionOptions,
)


class GeospatialWorkerError(RuntimeError):
    """Raised when the supervised Worker protocol is invalid."""


_IMAGERY_MEMORY_POOL = ImageryMemoryPool()
IMAGERY_PHASES = {
    "loading_model": (0.08, 0.10),
    "inferring": (0.10, 0.70),
    "building_preview": (0.70, 0.73),
    "vectorizing": (0.73, 0.85),
    "stitching": (0.85, 0.88),
    "merging": (0.88, 0.93),
    "writing_result": (0.93, 0.95),
}
GEOSPATIAL_PHASES = {
    "unpacking_source": (0.06, 0.14),
    "reading_inputs": (0.14, 0.30),
    "preparing_regions": (0.30, 0.90),
    "sampling_elevation": (0.30, 0.90),
    "writing_result": (0.90, 0.95),
}


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
        publish_region_source: Callable[[JobLease, str, Callable[[], None]], JobInfo] | None = None,
    ) -> None:
        config.validate()
        self.runtime = runtime
        self.config = config
        self._publish_region_source = publish_region_source
        if config.enable_imagery or config.imagery_model_path is not None:
            self.descriptor = WorkerDescriptor(
                worker_id="geospatial-worker",
                command_types=(*GEOSPATIAL_SUPPORTED_COMMANDS, IMAGERY_PREPARE_REGIONS_COMMAND),
            )
        self.worker_id = worker_id or f"geospatial_{uuid.uuid4().hex}"
        self._cancel_requests: set[str] = set()
        self._cancel_lock = threading.Lock()
        self._poller = WorkerJobPoller(self._run_next)
        config.runtime_root.mkdir(parents=True, exist_ok=True)
        config.cache_root.mkdir(parents=True, exist_ok=True)

    def request_cancel(self, job_id: str) -> None:
        with self._cancel_lock:
            self._cancel_requests.add(job_id)

    def run_once(self) -> JobInfo | None:
        return self._poller.poll()

    @property
    def active(self) -> bool:
        return self._poller.active

    def close(self) -> None:
        self._poller.close()

    def _run_next(self) -> Generator[None, None, JobInfo | None]:
        claimed = self.runtime.claim_next(
            self.worker_id,
            command_types=self.descriptor.command_types,
        )
        if claimed is None:
            return None
        return (yield from self._run_claimed(claimed))

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

    def _run_claimed(self, claimed: Any) -> Generator[None, None, JobInfo | None]:
        lease = claimed.lease
        imagery = claimed.job.command.command_type == IMAGERY_PREPARE_REGIONS_COMMAND
        attempt_dir = self.config.runtime_root / (
            f"{lease.job_id}-{lease.attempt_count}-{uuid.uuid4().hex[:8]}"
        )
        output_dir = attempt_dir / "outputs"
        command_path = attempt_dir / "command.json"
        result_path = attempt_dir / "result.json"
        progress_path = attempt_dir / "progress.json"
        stdout_path = attempt_dir / "stdout.log"
        stderr_path = attempt_dir / "stderr.log"
        process: subprocess.Popen[bytes] | None = None
        guard = None
        memory_reservation: ImageryMemoryReservation | None = None
        memory_limit_bytes: int | None = None
        process_registered = False
        terminated = False
        last_heartbeat = time.monotonic()
        heartbeat_interval = min(
            self.config.heartbeat_interval_seconds,
            self.runtime.lease_duration.total_seconds() / 3,
        )
        phases = IMAGERY_PHASES if imagery else GEOSPATIAL_PHASES

        def io_heartbeat(progress: float, stage: str) -> None:
            nonlocal last_heartbeat
            if self._is_cancelled(lease.job_id):
                raise _Cancelled()
            now = time.monotonic()
            if now - last_heartbeat >= heartbeat_interval:
                self.runtime.heartbeat(lease, progress=progress, stage=stage)
                last_heartbeat = now

        try:
            attempt_dir.mkdir(parents=True)
            output_dir.mkdir()
            self.runtime.heartbeat(lease, progress=0.05, stage="preparing_inputs")
            command = self._prepare_command(
                claimed.job.command.artifact_id,
                claimed.job.command.command_type,
                on_chunk=partial(io_heartbeat, 0.05, "preparing_inputs"),
            )
            _write_json(command_path, command)
            if imagery and os.name == "nt":
                while memory_reservation is None:
                    io_heartbeat(0.05, "preparing_inputs")
                    memory_reservation = _IMAGERY_MEMORY_POOL.try_reserve()
                    if memory_reservation is None:
                        yield
                memory_limit_bytes = memory_reservation.limit_bytes
            guard = ProcessTreeGuard(memory_limit_bytes=memory_limit_bytes)
            entry = Path(__file__).with_name("worker_entry.py")
            with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                process = subprocess.Popen(
                    [
                        str((self.config.imagery_python_executable if imagery else None)
                            or self.config.python_executable),
                        "-I",
                        *(("-X", "utf8") if imagery else ()),
                        str(entry),
                        *(_elevation_domain_args() if command.get("operation") == "sample_elevation" else ()),
                        "--command",
                        str(command_path),
                        "--output-dir",
                        str(output_dir),
                        "--result",
                        str(result_path),
                        "--progress",
                        str(progress_path),
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
                last_progress = started
                marker = None
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
                    current = _worker_progress(progress_path, phases)
                    if current is not None and (marker is None or current > marker):
                        marker, last_progress = current, now
                    if now - last_progress >= self.config.timeout_seconds:
                        terminated = True
                        guard.terminate(
                            process,
                            grace_seconds=self.config.termination_grace_seconds,
                        )
                        if imagery:
                            raise GeospatialDataError("imagery_worker_timeout", f"影像任务超过 {self.config.timeout_seconds:g} 秒没有处理进展，已停止并保留项目。请检查输入或分区重试。")
                        raise GeospatialDataError("geo_worker_timeout", f"地理数据任务超过 {self.config.timeout_seconds:g} 秒没有处理进展，已停止并保留项目。请查看任务诊断后重试。")
                    if now - last_heartbeat >= heartbeat_interval:
                        stage, progress = ("imagery_loading_model" if imagery else "processing_geospatial_data"), 0.06
                        if marker is not None:
                            phase_index, completed, total = marker
                            phase, (lo, hi) = tuple(phases.items())[phase_index]
                            stage = f"imagery_{phase}" if imagery else "processing_geospatial_data"
                            progress = lo + (hi - lo) * completed / max(1, total)
                        self.runtime.heartbeat(
                            lease,
                            progress=progress,
                            stage=stage,
                        )
                        self.runtime.store.heartbeat_worker_process(
                            lease.job_id,
                            lease.attempt_count,
                            _now_text(),
                        )
                        last_heartbeat = now
                    yield
                if process.poll() is None:
                    process.wait(timeout=self.config.termination_grace_seconds)
            if memory_reservation is not None:
                # Close the whole tree before granting its capacity elsewhere.
                guard.close()
                guard = None
                memory_reservation.close()
                memory_reservation = None
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
                return self._acknowledge_cancel(lease)
            if not result_path.is_file():
                if imagery:
                    raise GeospatialDataError("imagery_worker_exited", "影像处理进程提前退出，未保存候选区域。请检查运行环境或裁剪影像后重试。")
                raise GeospatialDataError("geo_worker_exited", "地理数据处理进程提前退出，未生成完整结果。项目已保留；请减少数据量后重试，详细原因可查看任务诊断。")
            result = _read_json(result_path)
            if process.returncode != 0 or result.get("status") != "succeeded":
                code = str(result.get("error_code") or "GEOSPATIAL_OPERATION_FAILED")
                message = str(result.get("message") or "地理数据处理失败，项目已保留，请查看任务诊断并检查输入数据。")
                if imagery and code == "imagery_memory_exhausted" and memory_limit_bytes is not None:
                    message = (
                        f"影像处理内存不足，本次进程预算约 {memory_limit_bytes / 1024**3:.1f} GiB。"
                        "未发布候选区域，项目已保留；请释放内存后重试，仍不足时可分区识别。"
                    )
                return self.runtime.fail(
                    lease,
                    JobErrorInfo(
                        code=code,
                        message=message,
                        retryable=code in {"geo_elevation_disk_full", "geo_disk_budget", "imagery_memory_exhausted"},
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
            self.runtime.heartbeat(lease, progress=0.95, stage="persisting_result")
            last_heartbeat = time.monotonic()
            persist_heartbeat = partial(io_heartbeat, 0.95, "persisting_result")
            artifact_id = self._register_result(
                result,
                output_dir,
                job_id=lease.job_id,
                attempt_number=lease.attempt_count,
                on_chunk=persist_heartbeat,
            )
            persist_heartbeat()
            if expected_operation == "prepare_regions" and command["version"] == REGION_SOURCE_AUTO_PUBLISH_VERSION:
                assert self._publish_region_source is not None
                self.runtime.heartbeat(lease, progress=0.98, stage="publishing_regions")

                def check_cancelled() -> None:
                    if self._is_cancelled(lease.job_id):
                        raise _Cancelled()

                try:
                    completed = self._publish_region_source(lease, artifact_id, check_cancelled)
                except RegionSourceImportError as error:
                    raise GeospatialDataError(
                        "geo_region_publication_failed",
                        "区域已计算，但无法完成定版。原始数据仍保留，请查看任务诊断。",
                    ) from error
            else:
                completed = self.runtime.succeed(lease, result_artifact_id=artifact_id)
            if imagery:
                try:
                    Path(command["mask_checkpoint"]).unlink(missing_ok=True)
                except OSError:
                    pass  # The result is committed; the cache can be reused or replaced.
            return completed
        except _Cancelled:
            return self._acknowledge_cancel(lease)
        except JobLeaseConflictError:
            return None
        except Exception as error:
            if guard is not None and process is not None and process.poll() is None:
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
                process_registered = False
            if self._is_cancelled(lease.job_id):
                return self._acknowledge_cancel(lease)
            code = (
                error.code if isinstance(error, GeospatialDataError)
                else "imagery_memory_exhausted" if imagery and is_host_memory_error(error)
                else "geo_disk_budget" if (
                    (isinstance(error, OSError) and error.errno == errno.ENOSPC)
                    or (isinstance(error, sqlite3.Error) and getattr(error, "sqlite_errorcode", None) == sqlite3.SQLITE_FULL)
                )
                else "GEOSPATIAL_SUPERVISOR_FAILED"
            )
            message = (
                str(error) if isinstance(error, GeospatialDataError)
                else "影像任务无法取得足够的系统内存，项目已保留。请释放内存后重试。" if code == "imagery_memory_exhausted"
                else "项目磁盘空间不足，无法完成地理数据任务。请释放空间后重试。" if code == "geo_disk_budget"
                else "地理数据任务未能正常完成，项目已保留。请检查输入文件和运行环境，详细原因可查看任务诊断。"
            )
            try:
                return self.runtime.fail(
                    lease,
                    JobErrorInfo(
                        code=code,
                        message=message,
                        retryable=code in {
                            "geo_worker_timeout", "geo_worker_exited", "geo_disk_budget",
                            "imagery_memory_pressure", "imagery_memory_exhausted",
                        },
                        diagnostic_artifact_id=self._diagnostic(
                            stdout_path,
                            stderr_path,
                            str(error.__cause__ or error),
                            process.returncode if process is not None else None,
                        ),
                    ),
                )
            except JobLeaseConflictError:
                return None
        finally:
            try:
                if guard is not None and process is not None and process.poll() is None:
                    guard.terminate(process, grace_seconds=self.config.termination_grace_seconds)
                if process_registered and process is not None:
                    self.runtime.store.finish_worker_process(
                        lease.job_id, lease.attempt_count,
                        status=WorkerProcessStatus.TERMINATED,
                        finished_at=_now_text(), exit_code=process.returncode,
                    )
            finally:
                try:
                    if guard is not None:
                        guard.close()
                finally:
                    if memory_reservation is not None:
                        memory_reservation.close()
                with self._cancel_lock:
                    self._cancel_requests.discard(lease.job_id)
                shutil.rmtree(attempt_dir, ignore_errors=True)

    def _prepare_command(
        self,
        command_artifact_id: str,
        command_type: str,
        *,
        on_chunk: Callable[[], None],
    ) -> dict[str, Any]:
        with self.runtime.store.open_artifact(command_artifact_id) as stream:
            command = json.load(stream)
        if not isinstance(command, dict):
            raise GeospatialWorkerError("Geospatial command must be an object")
        operation = _operation(command_type)
        prepares_regions = operation in {"prepare_regions", "prepare_imagery_regions"}
        expected_schema = (
            REGION_SOURCE_COMMAND_SCHEMA
            if prepares_regions
            else ELEVATION_SAMPLING_COMMAND_SCHEMA
        )
        expected_version = (
            REGION_SOURCE_COMMAND_VERSION
            if prepares_regions
            else ELEVATION_SAMPLING_COMMAND_VERSION
        )
        explicit_sampling = operation == "sample_elevation" and str(command.get("version")) == "2"
        auto_publish = (
            operation == "prepare_regions"
            and command.get("version") == REGION_SOURCE_AUTO_PUBLISH_VERSION
            and self._publish_region_source is not None
        )
        if (
            command.get("schema") != expected_schema
            or (str(command.get("version")) != expected_version and not (explicit_sampling or auto_publish))
            or command.get("operation") != ("prepare_regions" if prepares_regions else operation)
        ):
            raise GeospatialWorkerError("Geospatial command schema is unsupported")
        source_id = _required_text(command, "source_id")
        source = self.runtime.store.get_source(source_id)
        if not explicit_sampling and source.kind is not SourceKind.ASSEMBLY:
            raise GeospatialWorkerError("Geospatial source must be an Assembly")
        if _required_text(command, "source_artifact_id") != source.artifact_id:
            raise GeospatialWorkerError("Geospatial Source Artifact differs")
        prepared = dict(command)
        prepared["sources"] = []
        cache_source = partial(self._cache_artifact, on_chunk=on_chunk)
        if operation == "prepare_imagery_regions":
            return self._prepare_imagery_command(prepared, source_id, cache_source)
        if explicit_sampling:
            from verdflow.contracts import SamplingBinding
            binding = SamplingBinding.from_document(command["sampling_binding"])
            if (source.kind is not SourceKind.GEOTIFF or binding.source.kind != "source"
                    or binding.source.source_id != source_id
                    or binding.source.artifact_id != source.artifact_id
                    or binding.source.content_hash != self.runtime.store.get_artifact(source.artifact_id).content_hash):
                raise GeospatialWorkerError("Explicit elevation binding must reference the frozen GeoTIFF")
            prepared["sources"].append({
                "source_member_id": binding.source.source_member_id or source_id,
                "source_id": source_id, "kind": source.kind.value,
                "source_path": str(cache_source(source.artifact_id, source.kind)),
            })
        for member in (() if explicit_sampling else self.runtime.store.list_source_assembly_members(source_id)):
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
                    "source_path": str(cache_source(leaf.artifact_id, leaf.kind)),
                    "source_to_scene_matrix_row_major": list(
                        member.source_to_scene_matrix_row_major
                    ),
                }
            )
        if operation == "sample_elevation":
            points_artifact_id = _required_text(command, "points_artifact_id")
            parent = self.runtime.store.get_point_set(_required_text(command, "parent_point_set_id"))
            points_artifact = self.runtime.store.get_artifact(points_artifact_id)
            if (
                parent.kind is not PointSetKind.GENERATED
                or parent.point_artifact_id != points_artifact_id
                or points_artifact.schema_id != "verdflow.points"
                or points_artifact.schema_version != "2"
            ):
                raise GeospatialWorkerError("Elevation input must reference the frozen generated PointSet v2")
            prepared["point_count"] = parent.point_count
            prepared["points_path"] = str(
                self._cache_artifact_by_ref(points_artifact, ".json", on_chunk=on_chunk)
            )
        return prepared

    def _prepare_imagery_command(
        self, command: dict[str, Any], source_id: str,
        cache_source: Callable[[str, SourceKind], Path],
    ) -> dict[str, Any]:
        if command.get("provider") != {"id": IMAGERY_REGION_PROVIDER_ID, "version": "1"}:
            raise GeospatialWorkerError("Imagery Region Source Provider differs")
        model_path = self.config.imagery_model_path
        if model_path is None or not model_path.is_file():
            raise GeospatialDataError("imagery_model_missing", "未配置已验证的影像识别模型，不能开始识别。")
        options = ImageryRegionOptions.from_document(command.get("options"))
        members = {member.source_member_id: member for member in self.runtime.store.list_source_assembly_members(source_id)}
        member = members.get(options.imagery_member_id)
        if member is None:
            raise GeospatialDataError("imagery_source_missing", "影像来源不属于本次输入，请重新选择影像。")
        source = self.runtime.store.get_source(member.source_id)
        if source.kind is not SourceKind.GEOTIFF or tuple(member.source_to_scene_matrix_row_major) != (
            1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1.,
        ):
            raise GeospatialDataError("imagery_source_invalid", "请选择已对齐、无额外变换的 GeoTIFF 影像。")
        command.update(
            operation="prepare_imagery_regions",
            model_path=str(model_path.resolve()),
            mask_checkpoint=str(self.config.runtime_root.resolve() / "imagery-mask-checkpoint.tif"),
            sources=[{
                "source_member_id": member.source_member_id,
                "source_content_hash": source.content_hash,
                "source_path": str(cache_source(source.artifact_id, source.kind)),
            }],
        )
        if options.elevation_member_id is not None:
            elevation = members.get(options.elevation_member_id)
            if elevation is None:
                raise GeospatialDataError("imagery_elevation_missing", "高程来源不属于本次输入。")
            terrain = self.runtime.store.get_source(elevation.source_id)
            if (terrain.kind is not SourceKind.GEOTIFF or terrain.source_id == source.source_id
                    or terrain.content_hash == source.content_hash
                    or elevation.source_to_scene_matrix_row_major != member.source_to_scene_matrix_row_major):
                raise GeospatialDataError("imagery_elevation_invalid", "请选择与影像分开、已对齐的高程 GeoTIFF。")
            command["elevation_path"] = str(cache_source(terrain.artifact_id, terrain.kind))
        return command

    def _cache_artifact(
        self, artifact_id: str, kind: SourceKind, *,
        on_chunk: Callable[[], None],
    ) -> Path:
        extensions = {
            SourceKind.SHAPEFILE: ".zip",
            SourceKind.GEOJSON: ".geojson",
            SourceKind.GEOTIFF: ".tif",
        }
        artifact = self.runtime.store.get_artifact(artifact_id)
        return self._cache_artifact_by_ref(artifact, extensions[kind], on_chunk=on_chunk)

    def _cache_artifact_by_ref(
        self, artifact: Any, extension: str, *, on_chunk: Callable[[], None],
    ) -> Path:
        digest = artifact.content_hash.removeprefix("sha256:")
        destination = self.config.cache_root / f"{digest}{extension}"
        if destination.is_file() and destination.stat().st_size == artifact.byte_size:
            return destination
        destination.unlink(missing_ok=True)
        if shutil.disk_usage(self.config.cache_root).free < artifact.byte_size + MIN_FREE_DISK_BYTES:
            raise GeospatialDataError("geo_disk_budget", "项目磁盘空间不足，无法准备地理数据或点位文件。请释放空间后重试。")
        return self.runtime.store.materialize_artifact(artifact.artifact_id, destination, on_chunk=on_chunk)

    def _register_result(
        self,
        result: dict[str, Any],
        output_dir: Path,
        *,
        job_id: str,
        attempt_number: int,
        on_chunk: Callable[[], None],
    ) -> str:
        descriptors = result.get("output_files")
        if not isinstance(descriptors, list):
            raise GeospatialWorkerError("Worker output_files must be an array")
        outputs: list[dict[str, Any]] = []
        roles: set[str] = set()
        root = output_dir.resolve()
        for descriptor in descriptors:
            on_chunk()
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
                on_chunk=on_chunk,
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
                if result.get("operation") in {"prepare_regions", "prepare_imagery_regions"}
                else "elevation_sampling"
            ),
            "source_id": _required_text(result, "source_id"),
            "job_id": job_id,
            "attempt_number": attempt_number,
            "producer": {
                "id": (IMAGERY_REGION_PROVIDER_ID if result.get("operation") == "prepare_imagery_regions"
                       else GEOSPATIAL_ADAPTER_ID),
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
    ) -> str | None:
        try:
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
        except (OSError, sqlite3.OperationalError):
            return None  # A diagnostic blob must not replace the task's original error.
        return artifact.artifact_id

    def _is_cancelled(self, job_id: str) -> bool:
        with self._cancel_lock:
            return job_id in self._cancel_requests

    def _acknowledge_cancel(self, lease: JobLease) -> JobInfo | None:
        try:
            self.runtime.request_cancel(lease.job_id)
            return self.runtime.acknowledge_cancel(lease)
        except JobLeaseConflictError:
            return None

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
        IMAGERY_PREPARE_REGIONS_COMMAND: "prepare_imagery_regions",
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


class _Cancelled(Exception):
    pass


def _worker_progress(
    path: Path, phases: dict[str, tuple[float, float]],
) -> tuple[int, int, int] | None:
    try:
        with path.open("rb") as stream:
            phase, completed, total = json.loads(stream.read(512))
        if type(completed) is int and type(total) is int and 0 <= completed <= total:
            return tuple(phases).index(phase), completed, total
    except (OSError, ValueError, TypeError):
        pass
    return None


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
        with path.open("rb") as stream:
            stream.seek(max(0, os.fstat(stream.fileno()).st_size - limit))
            return stream.read(limit).decode("utf-8", errors="replace")
    except OSError:
        return ""


def _elevation_domain_args() -> tuple[str, ...]:
    # Source checkouts need the same trusted modules already imported by the host.
    import verdflow.contracts
    import verdflow.planting

    roots = dict.fromkeys(
        Path(module.__file__).resolve().parents[2]
        for module in (verdflow.contracts, verdflow.planting)
    )
    return tuple(value for root in roots for value in ("--domain-root", str(root)))


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
