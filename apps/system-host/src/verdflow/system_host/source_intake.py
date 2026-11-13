"""Machine-local file intake and the first durable workbench handoff."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import errno
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
from tempfile import NamedTemporaryFile
from time import monotonic
from typing import Literal
from uuid import uuid4

from verdflow.blender_worker import BLENDER_WORKER_ADAPTER_ID
from verdflow.contracts import (
    ArtifactRef, JobCommandRef, JobErrorInfo, JobInfo, JobStatus, MaterialConfirmationMode, MaterialSelectionMode, SourceKind,
)
from verdflow.core import RegionSourceRequest, ScenePreparationRequest, SourceRegistration, WorkerDescriptor
from verdflow.core_host import CoreHost
from verdflow.geospatial_worker import GeospatialDataError, IMAGERY_REGION_PROVIDER_ID, shapefile_members, write_shapefile_package
from verdflow.planting import canonical_json_bytes
from verdflow.project_store import ArtifactNotFoundError, JobLeaseConflictError

from .imagery_input import imagery_request


SOURCE_INTAKE_COMMAND = "verdflow.studio.prepare-local-sources"
_ACTIVE = {JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.CANCELLING}
_MEDIA_TYPES = {
    SourceKind.FBX: "model/vnd.autodesk.fbx", SourceKind.GEOTIFF: "image/tiff",
    SourceKind.GEOJSON: "application/geo+json", SourceKind.SHAPEFILE: "application/vnd.verdflow.shapefile+zip",
}


class SourceIntakeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class _IntakeCancelled(Exception):
    pass


@dataclass(frozen=True, slots=True)
class SourceIntakeRequest:
    workflow: Literal["fbx", "vector", "imagery"]
    source_path: Path
    elevation_path: Path | None = None
    selection_mode: MaterialSelectionMode = MaterialSelectionMode.MANUAL
    confirmation_mode: MaterialConfirmationMode = MaterialConfirmationMode.MANUAL
    analysis_provider_id: str | None = None


@dataclass(frozen=True, slots=True)
class _FileStamp:
    path: str
    size: int
    modified_ns: int
    device: int
    inode: int

    @classmethod
    def capture(cls, path: Path) -> _FileStamp:
        try:
            result = path.stat()
            if not stat.S_ISREG(result.st_mode):
                raise FileNotFoundError(path.name)
        except OSError as error:
            raise SourceIntakeError("source_file_unreadable", f"无法读取 {path.name}。请确认文件存在且当前用户有读取权限。") from error
        return cls(str(path), result.st_size, result.st_mtime_ns, result.st_dev, result.st_ino)


@dataclass(frozen=True, slots=True)
class _SourceSnapshot:
    kind: SourceKind
    path: str
    files: tuple[_FileStamp, ...]

    @classmethod
    def capture(cls, path: Path, kind: SourceKind) -> _SourceSnapshot:
        path = path.resolve()
        members = shapefile_members(path) if kind is SourceKind.SHAPEFILE else (path,)
        return cls(kind, str(path), tuple(_FileStamp.capture(file) for file in members))

    def verify(self) -> None:
        if self != self.capture(Path(self.path), self.kind):
            raise SourceIntakeError("source_file_changed", f"{Path(self.path).name} 在登记前或登记过程中被修改。请确认文件已保存后重试。")


@dataclass(frozen=True, slots=True)
class _IntakeSnapshot:
    token: str
    workflow: str
    selection_mode: MaterialSelectionMode
    confirmation_mode: MaterialConfirmationMode
    analysis_provider_id: str | None
    imagery_backend: str
    sources: tuple[_SourceSnapshot, ...]
    staged_artifacts: tuple[str, ...] = ()


class SourceIntakeStore:
    """Keep external paths out of the project DB, commands, and results."""

    def __init__(self, root: Path, imagery_backend: str) -> None:
        self.root = root
        self.imagery_backend = imagery_backend

    def _path(self, project_id: str, project_path: Path) -> Path:
        identity = project_id + "\0" + os.path.normcase(str(project_path.resolve()))
        return self.root / (hashlib.sha256(identity.encode("utf-8")).hexdigest() + ".json")

    def forget(self, project_id: str, project_path: Path) -> None:
        try:
            self._path(project_id, project_path).unlink(missing_ok=True)
        except OSError:
            pass  # Reconcile a completed record when this project next opens.

    def _persist(self, path: Path, snapshot: _IntakeSnapshot) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as output:
            scratch = Path(output.name)
            try:
                output.write(json.dumps(asdict(snapshot), ensure_ascii=False, allow_nan=False).encode("utf-8"))
                output.flush()
                os.fsync(output.fileno())
                output.close()
                os.replace(scratch, path)
            finally:
                output.close()
                scratch.unlink(missing_ok=True)

    def _load(self, host: CoreHost, job: JobInfo) -> _IntakeSnapshot:
        with host.store.open_artifact(job.command.artifact_id) as stream:
            command = json.load(stream)
        path = self._path(host.store.project_info().project_id, host.store.root)
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            if (command["schema"] != SOURCE_INTAKE_COMMAND or command["version"] != "1"
                    or document["token"] != command["input_token"] or document["workflow"] != command["workflow"]):
                raise ValueError("Unmatched intake snapshot")
            return _IntakeSnapshot(
                document["token"], document["workflow"], MaterialSelectionMode(document["selection_mode"]),
                MaterialConfirmationMode(document["confirmation_mode"]), document["analysis_provider_id"], document["imagery_backend"],
                tuple(_SourceSnapshot(SourceKind(source["kind"]), source["path"],
                    tuple(_FileStamp(**item) for item in source["files"])) for source in document["sources"]),
                tuple(document.get("staged_artifacts", ())),
            )
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise SourceIntakeError("source_input_unavailable", "本机没有可恢复的源文件登记记录，请重新选择项目输入。") from error

    @staticmethod
    def _latest(host: CoreHost) -> JobInfo | None:
        jobs = host.store.list_jobs(command_types=(SOURCE_INTAKE_COMMAND,), limit=1).items
        return jobs[0] if jobs else None

    def submit(self, host: CoreHost, request: SourceIntakeRequest, key: str) -> JobInfo:
        latest = self._latest(host)
        if latest and latest.idempotency_key == key:
            return latest
        if latest and (latest.status in _ACTIVE or latest.status is JobStatus.SUCCEEDED):
            raise SourceIntakeError("source_intake_exists", "当前项目已提交输入，不能重复登记。")
        self._require_unfixed(host)
        suffix = request.source_path.suffix.lower()
        if request.workflow == "fbx" and suffix == ".fbx" and request.elevation_path is None:
            first_kind = SourceKind.FBX
        elif request.workflow == "imagery" and suffix in {".tif", ".tiff"}:
            first_kind = SourceKind.GEOTIFF
        elif request.workflow == "vector" and suffix in {".shp", ".geojson", ".json"}:
            first_kind = SourceKind.SHAPEFILE if suffix == ".shp" else SourceKind.GEOJSON
        else:
            raise SourceIntakeError("source_kind_invalid", "所选文件与当前工作流程不匹配。")
        if request.workflow != "fbx" and (request.elevation_path is None or request.elevation_path.suffix.lower() not in {".tif", ".tiff"}):
            raise SourceIntakeError("source_elevation_required", "请选择与输入对齐的高程 GeoTIFF。")
        if request.selection_mode is MaterialSelectionMode.AI_ASSISTED and not request.analysis_provider_id:
            raise SourceIntakeError("source_provider_required", "请选择用于材质分析的模型服务。")
        self._validate_workflow(host, request.workflow)
        sources = [_SourceSnapshot.capture(request.source_path, first_kind)]
        if request.elevation_path is not None:
            sources.append(_SourceSnapshot.capture(request.elevation_path, SourceKind.GEOTIFF))
        self._check_distinct_sources(tuple(sources))
        return self._queue(host, _IntakeSnapshot(uuid4().hex, request.workflow, request.selection_mode,
            request.confirmation_mode, request.analysis_provider_id, self.imagery_backend, tuple(sources)), key)

    def retry(self, host: CoreHost, job_id: str) -> JobInfo:
        latest = self._latest(host)
        if not latest or latest.job_id != job_id or latest.status not in {JobStatus.FAILED, JobStatus.INTERRUPTED, JobStatus.CANCELLED}:
            raise SourceIntakeError("source_intake_not_retryable", "当前登记任务不能重试。")
        self._require_unfixed(host)
        snapshot = self._load(host, latest)
        refreshed = tuple(_SourceSnapshot.capture(Path(source.path), source.kind) for source in snapshot.sources)
        self._check_distinct_sources(refreshed)
        reuse_count = 0
        for old, new in zip(snapshot.sources, refreshed, strict=True):
            if old != new:
                break
            reuse_count += 1
        snapshot = replace(snapshot, token=uuid4().hex, sources=refreshed, staged_artifacts=snapshot.staged_artifacts[:reuse_count])
        self._validate_workflow(host, snapshot.workflow)
        return self._queue(host, snapshot, f"source-intake:retry:{uuid4().hex}")

    @staticmethod
    def _check_distinct_sources(sources: tuple[_SourceSnapshot, ...]) -> None:
        if len(sources) != 2:
            return
        image_file, terrain_file = sources[0].files[0], sources[1].files[0]
        if (sources[0].path == sources[1].path or (image_file.inode and
                (image_file.device, image_file.inode) == (terrain_file.device, terrain_file.inode))):
            raise SourceIntakeError("source_elevation_distinct", "影像与高程须使用两个不同的 GeoTIFF 文件。")

    @staticmethod
    def _validate_workflow(host: CoreHost, workflow: str) -> None:
        try:
            if workflow == "fbx":
                host.workflows.preparation("region_extraction", BLENDER_WORKER_ADAPTER_ID)
            else:
                host.workflows.preparation("region_source", IMAGERY_REGION_PROVIDER_ID if workflow == "imagery" else "verdflow.geospatial")
        except (RuntimeError, ValueError, KeyError) as error:
            raise SourceIntakeError("source_workflow_unavailable", "当前项目未启用此输入流程所需的处理环境。") from error

    @staticmethod
    def _require_unfixed(host: CoreHost) -> None:
        if host.store.project_workflow_state().source_id is not None:
            raise SourceIntakeError("source_already_fixed", "项目已有固定来源，不能覆盖当前输入。")

    def _queue(self, host: CoreHost, snapshot: _IntakeSnapshot, key: str) -> JobInfo:
        store = host.store
        path = self._path(store.project_info().project_id, store.root)
        command = {"schema": SOURCE_INTAKE_COMMAND, "version": "1", "input_token": snapshot.token, "workflow": snapshot.workflow}
        artifact = store.put_bytes(canonical_json_bytes(command), media_type="application/vnd.verdflow.command+json",
            schema_id=SOURCE_INTAKE_COMMAND, schema_version="1")
        self._persist(path, snapshot)
        return host.runtime.submit(JobCommandRef(SOURCE_INTAKE_COMMAND, "1", artifact.artifact_id),
            idempotency_key=key, max_attempts=2)

    def checkpoint(self, host: CoreHost, snapshot: _IntakeSnapshot, artifacts: list[ArtifactRef]) -> None:
        path = self._path(host.store.project_info().project_id, host.store.root)
        try:
            self._persist(path, replace(snapshot, staged_artifacts=tuple(artifact.artifact_id for artifact in artifacts)))
        except OSError:
            pass  # The checkpoint is optional; failure must not prevent publication.


class SourceIntakeWorker:
    descriptor = WorkerDescriptor("studio-source-intake", (SOURCE_INTAKE_COMMAND,))

    def __init__(self, host: CoreHost, inputs: SourceIntakeStore) -> None:
        self.host = host
        self.inputs = inputs
        self._cancel_requests: set[str] = set()

    def request_cancel(self, job_id: str) -> None:
        self._cancel_requests.add(job_id)

    def reconcile(self) -> int:
        latest = self.inputs._latest(self.host)
        if latest and latest.status is JobStatus.SUCCEEDED:
            self.inputs.forget(self.host.store.project_info().project_id, self.host.store.root)
        return 0

    def _staged_artifact(self, snapshot: _IntakeSnapshot, ordinal: int, media_type: str) -> ArtifactRef | None:
        if ordinal >= len(snapshot.staged_artifacts):
            return None
        store = self.host.store
        try:
            ref = store.get_artifact(snapshot.staged_artifacts[ordinal])
            with store.open_artifact(ref.artifact_id) as stream:
                if ref.media_type == media_type and os.fstat(stream.fileno()).st_size == ref.byte_size:
                    return ref
        except (ArtifactNotFoundError, OSError):
            pass
        return None

    def run_once(self) -> JobInfo | None:
        host = self.host
        store, runtime = host.store, host.runtime
        claimed = runtime.claim_next(self.descriptor.worker_id, command_types=self.descriptor.command_types)
        if claimed is None:
            return None
        lease = claimed.lease
        try:
            self.inputs._require_unfixed(host)
            snapshot = self.inputs._load(host, claimed.job)
            runtime.heartbeat(lease, progress=0.0, stage="copying_project_sources")
            last = monotonic()
            completed = 0
            total = max(1, sum(file.size for source in snapshot.sources for file in source.files))

            def checkpoint(copied: int | None = None) -> None:
                nonlocal last
                if lease.job_id in self._cancel_requests:
                    raise _IntakeCancelled()
                now = monotonic()
                if now - last >= min(2, runtime.lease_duration.total_seconds() / 3):
                    progress = min(0.9, 0.9 * (completed + copied) / total) if copied is not None else None
                    runtime.heartbeat(lease, progress=progress)
                    last = now

            artifacts: list[ArtifactRef] = []
            for ordinal, source in enumerate(snapshot.sources):
                checkpoint()
                source.verify()
                path = Path(source.path)
                media = _MEDIA_TYPES[source.kind]
                artifact = self._staged_artifact(snapshot, ordinal, media)
                copied = artifact is None
                if artifact is None and source.kind is SourceKind.SHAPEFILE:
                    artifact = store.write_artifact(lambda output: write_shapefile_package(path, output, on_progress=checkpoint),
                        media_type=media, on_chunk=checkpoint)
                elif artifact is None:
                    artifact = store.put_file(path, media_type=media, on_progress=checkpoint)
                source.verify()
                artifacts.append(artifact)
                if copied:
                    self.inputs.checkpoint(host, snapshot, artifacts)
                completed += sum(file.size for file in source.files)
            checkpoint()
            runtime.heartbeat(lease, progress=0.95, stage="starting_project_workflow")
            with store.atomic_write():
                self.inputs._require_unfixed(host)
                registered = tuple(host.application.register_source(SourceRegistration(
                    kind=source.kind, original_name=(f"{Path(source.path).stem}.shapefile.zip"
                        if source.kind is SourceKind.SHAPEFILE else Path(source.path).name),
                    artifact_id=artifact.artifact_id,
                )) for source, artifact in zip(snapshot.sources, artifacts, strict=True))
                follow_key = f"source-intake:{lease.job_id}:prepare"
                if snapshot.workflow == "fbx":
                    prepared = host.submit_current_scene_preparation(ScenePreparationRequest(
                        registered[0].source_id, snapshot.analysis_provider_id, BLENDER_WORKER_ADAPTER_ID,
                        snapshot.confirmation_mode, selection_mode=snapshot.selection_mode,
                    ), idempotency_key=follow_key)
                    next_id = prepared.execution_id
                else:
                    assembly = host.create_identity_assembly(
                        original_name=" + ".join(Path(source.path).name for source in snapshot.sources),
                        source_ids=tuple(source.source_id for source in registered),
                    )
                    if snapshot.workflow == "imagery":
                        request = imagery_request(store, RegionSourceRequest(assembly.source_id, IMAGERY_REGION_PROVIDER_ID, {
                            "imagery_source_id": registered[0].source_id, "elevation_source_id": registered[1].source_id,
                        }), snapshot.imagery_backend)
                    else:
                        request = RegionSourceRequest(assembly.source_id, "verdflow.geospatial", auto_publish=True)
                    next_id = host.submit_region_source(request, idempotency_key=follow_key).job_id
                schema = SOURCE_INTAKE_COMMAND + ".result"
                result = store.put_bytes(canonical_json_bytes({"schema": schema, "version": "1", "preparation_id": next_id,
                    "source_ids": [source.source_id for source in registered]}), media_type="application/json",
                    schema_id=schema, schema_version="1")
                checkpoint()
                job = runtime.succeed(lease, result_artifact_id=result.artifact_id)
            self.inputs.forget(store.project_info().project_id, store.root)
            return job
        except JobLeaseConflictError:
            return None
        except _IntakeCancelled:
            try:
                runtime.request_cancel(lease.job_id)
                return runtime.acknowledge_cancel(lease)
            except JobLeaseConflictError:
                return None
        except (RuntimeError, ValueError, KeyError, OSError, sqlite3.Error, MemoryError) as error:
            if getattr(error, "errno", None) == errno.ENOSPC or getattr(error, "sqlite_errorcode", None) == sqlite3.SQLITE_FULL:
                code, message = "source_disk_full", "项目磁盘空间不足，未登记任何不完整的来源。释放空间后可重试。"
            elif isinstance(error, (SourceIntakeError, GeospatialDataError)):
                code, message = error.code, str(error)
            elif isinstance(error, OSError):
                code, message = "source_file_unreadable", "读取或保存源文件失败，请检查原文件和项目磁盘。"
            else:
                code, message = "source_intake_failed", str(error) or "登记源文件未完成，项目已保留。"
            try:
                return runtime.fail(lease, JobErrorInfo(code, message, retryable=False))
            except JobLeaseConflictError:
                return None
        finally:
            self._cancel_requests.discard(lease.job_id)
