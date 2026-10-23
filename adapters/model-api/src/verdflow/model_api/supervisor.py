"""Supervise one non-retryable Model API Job attempt."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from typing import Any, Callable
import uuid

from verdflow.contracts import (
    MODEL_ANALYSIS_COMMAND_SCHEMA,
    MODEL_ANALYSIS_COMMAND_VERSION,
    MODEL_ANALYSIS_RESULT_SCHEMA,
    MODEL_ANALYSIS_RESULT_VERSION,
    JobErrorInfo,
    JobInfo,
    JobStatus,
    WorkerProcessInfo,
    WorkerProcessStatus,
)
from verdflow.core import JobRuntime
from verdflow.project_store import ProcessStatus, SystemProcessInspector
from verdflow.worker_runtime import ProcessTreeGuard, terminate_process_by_pid

from .models import ModelApiProviderConfig
from .protocol import (
    MODEL_API_ANALYZE_MATERIALS_COMMAND,
    MODEL_API_KEY_ENVIRONMENT,
    MODEL_API_SUPPORTED_COMMANDS,
    MODEL_API_WORKER_ADAPTER_ID,
    MODEL_API_WORKER_PROTOCOL_VERSION,
    ModelApiWorkerConfig,
    ModelApiWorkerError,
)
from .provider_config import ModelApiProviderRegistry
from .secrets import SecretResolver


class ModelApiWorkerSupervisor:
    """Own process lifetime and keep credentials outside persistent artifacts."""

    def __init__(
        self,
        runtime: JobRuntime,
        provider_registry: ModelApiProviderRegistry,
        secret_resolver: SecretResolver,
        config: ModelApiWorkerConfig,
        *,
        worker_id: str | None = None,
        command_factory: Callable[[Path, Path], list[str]] | None = None,
        process_started: Callable[[WorkerProcessInfo], None] | None = None,
    ) -> None:
        config.validate()
        self.runtime = runtime
        self.provider_registry = provider_registry
        self.secret_resolver = secret_resolver
        self.config = config
        self.worker_id = worker_id or f"model_api_{uuid.uuid4().hex}"
        self._command_factory = command_factory
        self._process_started = process_started
        self._cancel_requests: set[str] = set()
        self._cancel_lock = threading.Lock()
        self.config.runtime_root.mkdir(parents=True, exist_ok=True)

    def request_cancel(self, job_id: str) -> None:
        with self._cancel_lock:
            self._cancel_requests.add(job_id)

    def run_once(self) -> JobInfo | None:
        claimed = self.runtime.claim_next(
            self.worker_id,
            command_types=MODEL_API_SUPPORTED_COMMANDS,
        )
        if claimed is None:
            return None
        return self._run_claimed(claimed)

    def reconcile_recorded_processes(self) -> int:
        inspector = SystemProcessInspector()
        recovered = 0
        for record in self.runtime.store.list_active_worker_processes():
            if record.adapter_id != MODEL_API_WORKER_ADAPTER_ID:
                continue
            probe = inspector.probe(record.host, record.pid)
            if probe.status is ProcessStatus.UNKNOWN:
                raise ModelApiWorkerError(
                    "Recorded Model API process identity cannot be inspected: "
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
            self._remove_attempt_dirs(record.job_id, record.attempt_number)
            recovered += 1
        return recovered

    def _run_claimed(self, claimed: Any) -> JobInfo:
        lease = claimed.lease
        control_dir = self._create_control_dir(lease.job_id, lease.attempt_count)
        process: subprocess.Popen[bytes] | None = None
        guard = ProcessTreeGuard()
        registered = False
        process_status = WorkerProcessStatus.EXITED
        failure: tuple[str, str] | None = None
        cancelled = False
        dispatched = False
        stdout_path = control_dir / "stdout.log"
        stderr_path = control_dir / "stderr.log"
        try:
            self.runtime.heartbeat(lease, progress=0.02, stage="preparing_inputs")
            analysis = self._load_analysis_command(
                claimed.job.command.artifact_id,
                claimed.job.command.command_type,
            )
            provider = self.provider_registry.get(
                _required_text(analysis, "provider_id")
            )
            self._validate_provider_lineage(analysis, provider)
            facts_path = control_dir / "material-facts.json"
            with self.runtime.store.open_artifact(
                _required_text(analysis, "input_artifact_id")
            ) as source, facts_path.open("wb") as target:
                shutil.copyfileobj(source, target)
            worker_command = {
                "schema": "verdflow.model-api-worker.command",
                "version": MODEL_API_WORKER_PROTOCOL_VERSION,
                "analysis": analysis,
                "provider": _provider_document(provider),
                "facts_file": facts_path.name,
                "request_timeout_seconds": self.config.request_timeout_seconds,
                "heartbeat_interval_seconds": self.config.heartbeat_interval_seconds,
            }
            command_path = control_dir / "command.json"
            _write_json(command_path, worker_command)
            api_key = self.secret_resolver.resolve(provider.secret_ref)
            environment = _worker_environment(api_key)
            self.runtime.heartbeat(lease, progress=0.04, stage="launching_model_worker")
            with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                process = subprocess.Popen(
                    self._command(control_dir, command_path),
                    cwd=control_dir,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    env=environment,
                    **guard.popen_options(),
                )
                environment[MODEL_API_KEY_ENVIRONMENT] = ""
                api_key = ""
                guard.attach(process)
                identity = self._child_identity(process.pid)
                record = WorkerProcessInfo(
                    job_id=lease.job_id,
                    attempt_number=lease.attempt_count,
                    adapter_id=MODEL_API_WORKER_ADAPTER_ID,
                    host=identity.host,
                    pid=identity.pid,
                    started_at_token=identity.started_at_token,
                    status=WorkerProcessStatus.RUNNING,
                    started_at=_now_text(),
                    heartbeat_at=None,
                    finished_at=None,
                    exit_code=None,
                )
                self.runtime.store.register_worker_process(record)
                registered = True
                if self._process_started is not None:
                    self._process_started(record)
                launched = time.monotonic()
                ready_at: float | None = None
                last_heartbeat = launched
                heartbeat_sequence = -1
                last_lease_refresh = launched
                while process.poll() is None:
                    now = time.monotonic()
                    if self._take_cancel(lease.job_id):
                        if self.runtime.get(lease.job_id).status is JobStatus.RUNNING:
                            self.runtime.request_cancel(lease.job_id)
                        cancelled = True
                        process_status = WorkerProcessStatus.TERMINATED
                        break
                    ready_path = control_dir / "ready.json"
                    if ready_at is None and ready_path.is_file():
                        ready = _read_protocol(
                            ready_path,
                            "verdflow.model-api-worker.ready",
                        )
                        if int(ready.get("pid", -1)) != process.pid:
                            raise ModelApiWorkerError("Model API ready PID differs")
                        ready_at = now
                        last_heartbeat = now
                    heartbeat_path = control_dir / "heartbeat.json"
                    if ready_at is not None and heartbeat_path.is_file():
                        heartbeat = _read_protocol(
                            heartbeat_path,
                            "verdflow.model-api-worker.heartbeat",
                        )
                        sequence = int(heartbeat.get("sequence", -1))
                        if sequence > heartbeat_sequence:
                            heartbeat_sequence = sequence
                            last_heartbeat = now
                            stamp = _now_text()
                            self.runtime.store.heartbeat_worker_process(
                                lease.job_id, lease.attempt_count, stamp
                            )
                            self.runtime.heartbeat(
                                lease,
                                progress=float(heartbeat["progress"]),
                                stage=str(heartbeat["stage"]),
                            )
                            last_lease_refresh = now
                    dispatched = dispatched or (
                        control_dir / "request-dispatched.json"
                    ).is_file()
                    if now - last_lease_refresh >= min(
                        self.runtime.lease_duration.total_seconds() / 3.0,
                        1.0,
                    ):
                        self.runtime.heartbeat(lease)
                        last_lease_refresh = now
                    if (
                        ready_at is None
                        and now - launched > self.config.ready_timeout_seconds
                    ):
                        failure = (
                            "MODEL_API_READY_TIMEOUT",
                            "Model API worker did not publish ready before "
                            "the deadline",
                        )
                        process_status = WorkerProcessStatus.TERMINATED
                        break
                    if (
                        ready_at is not None
                        and now - last_heartbeat > self.config.heartbeat_timeout_seconds
                    ):
                        failure = (
                            "MODEL_API_HEARTBEAT_TIMEOUT",
                            "Model API worker heartbeat stopped",
                        )
                        process_status = WorkerProcessStatus.TERMINATED
                        break
                    if now - launched > self.config.execution_timeout_seconds:
                        failure = (
                            "MODEL_API_EXECUTION_TIMEOUT",
                            "Model API worker exceeded the execution deadline",
                        )
                        process_status = WorkerProcessStatus.TERMINATED
                        break
                    time.sleep(self.config.poll_interval_seconds)
                if process.poll() is None:
                    guard.terminate(
                        process,
                        grace_seconds=self.config.termination_grace_seconds,
                    )
                else:
                    process.wait()
                dispatched = dispatched or (
                    control_dir / "request-dispatched.json"
                ).is_file()
        except Exception as error:
            failure = ("MODEL_API_SUPERVISOR_ERROR", str(error) or type(error).__name__)
            process_status = WorkerProcessStatus.TERMINATED
            if process is not None and process.poll() is None:
                guard.terminate(
                    process,
                    grace_seconds=self.config.termination_grace_seconds,
                )
        finally:
            guard.close()

        exit_code = process.returncode if process is not None else None
        try:
            if registered:
                self.runtime.store.finish_worker_process(
                    lease.job_id,
                    lease.attempt_count,
                    status=process_status,
                    finished_at=_now_text(),
                    exit_code=exit_code,
                )
            if cancelled:
                return self.runtime.acknowledge_cancel(lease)
            if failure is not None:
                return self._fail(lease, control_dir, *failure, dispatched=dispatched)
            result_path = control_dir / "result.json"
            if exit_code != 0 or not result_path.is_file():
                worker_failure = self._worker_failure(control_dir)
                code = (
                    "MODEL_API_ATTEMPT_UNCERTAIN"
                    if dispatched
                    else "MODEL_API_PROCESS_FAILED"
                )
                return self._fail(
                    lease,
                    control_dir,
                    code,
                    worker_failure or f"Model API worker exited with code {exit_code}",
                    dispatched=dispatched,
                )
            result = _read_protocol(result_path, MODEL_ANALYSIS_RESULT_SCHEMA)
            if str(result.get("version")) != MODEL_ANALYSIS_RESULT_VERSION:
                raise ModelApiWorkerError(
                    "Model analysis result version is unsupported"
                )
            artifact = self.runtime.store.put_file(
                result_path,
                media_type="application/vnd.verdflow.model-analysis-result+json",
                schema_id=MODEL_ANALYSIS_RESULT_SCHEMA,
                schema_version=MODEL_ANALYSIS_RESULT_VERSION,
            )
            return self.runtime.succeed(lease, result_artifact_id=artifact.artifact_id)
        except Exception as error:
            if self.runtime.get(lease.job_id).status is not JobStatus.RUNNING:
                raise
            return self._fail(
                lease,
                control_dir,
                "MODEL_API_RESULT_INVALID",
                str(error) or type(error).__name__,
                dispatched=dispatched,
            )
        finally:
            with self._cancel_lock:
                self._cancel_requests.discard(lease.job_id)
            shutil.rmtree(control_dir, ignore_errors=True)

    def _load_analysis_command(
        self,
        artifact_id: str,
        command_type: str,
    ) -> dict[str, Any]:
        if command_type != MODEL_API_ANALYZE_MATERIALS_COMMAND:
            raise ModelApiWorkerError("Model API command type is unsupported")
        with self.runtime.store.open_artifact(artifact_id) as stream:
            value = json.load(stream)
        if not isinstance(value, dict):
            raise ModelApiWorkerError("Model analysis command must be an object")
        if value.get("schema") != MODEL_ANALYSIS_COMMAND_SCHEMA:
            raise ModelApiWorkerError("Model analysis command schema is unsupported")
        if str(value.get("version")) != MODEL_ANALYSIS_COMMAND_VERSION:
            raise ModelApiWorkerError("Model analysis command version is unsupported")
        return value

    @staticmethod
    def _validate_provider_lineage(
        command: dict[str, Any], provider: ModelApiProviderConfig
    ) -> None:
        expected = {
            "provider_id": provider.descriptor.provider_id,
            "provider_version": provider.descriptor.provider_version,
            "protocol": provider.descriptor.protocol.value,
        }
        mismatches = [
            key for key, value in expected.items()
            if command.get(key) != value
        ]
        if mismatches:
            raise ModelApiWorkerError(
                "Model Provider changed after Job submission: " + ", ".join(mismatches)
            )

    def _fail(
        self,
        lease: Any,
        control_dir: Path,
        code: str,
        message: str,
        *,
        dispatched: bool,
    ) -> JobInfo:
        diagnostic = {
            "schema": "verdflow.model-api-worker.diagnostic",
            "version": MODEL_API_WORKER_PROTOCOL_VERSION,
            "code": code,
            "message": message,
            "request_may_have_been_dispatched": dispatched,
            "automatic_retry_permitted": False,
        }
        path = control_dir / "diagnostic.json"
        _write_json(path, diagnostic)
        artifact = self.runtime.store.put_file(
            path,
            media_type="application/vnd.verdflow.diagnostic+json",
            schema_id="verdflow.model-api-worker.diagnostic",
            schema_version=MODEL_API_WORKER_PROTOCOL_VERSION,
        )
        return self.runtime.fail(
            lease,
            JobErrorInfo(
                code=code,
                message=message,
                retryable=False,
                diagnostic_artifact_id=artifact.artifact_id,
            ),
        )

    def _command(self, control_dir: Path, command_path: Path) -> list[str]:
        if self._command_factory is not None:
            return self._command_factory(control_dir, command_path)
        return [
            str(self.config.python_executable),
            "-m",
            "verdflow.model_api.worker_entry",
            "--control-dir",
            str(control_dir),
            "--command",
            str(command_path),
        ]

    def _create_control_dir(self, job_id: str, attempt: int) -> Path:
        path = self.config.runtime_root / f"{job_id}-{attempt}-{uuid.uuid4().hex[:8]}"
        path.mkdir(parents=False, exist_ok=False)
        return path

    def _take_cancel(self, job_id: str) -> bool:
        with self._cancel_lock:
            if job_id not in self._cancel_requests:
                return False
            self._cancel_requests.remove(job_id)
            return True

    def _remove_attempt_dirs(self, job_id: str, attempt: int) -> None:
        prefix = f"{job_id}-{attempt}-"
        for path in self.config.runtime_root.iterdir():
            if path.is_dir() and path.name.startswith(prefix):
                shutil.rmtree(path, ignore_errors=True)

    @staticmethod
    def _child_identity(pid: int) -> Any:
        inspector = SystemProcessInspector()
        current = inspector.current_identity()
        probe = inspector.probe(current.host, pid)
        if probe.status is not ProcessStatus.ALIVE or probe.identity is None:
            raise ModelApiWorkerError("Could not establish Model API process identity")
        return probe.identity

    @staticmethod
    def _worker_failure(control_dir: Path) -> str | None:
        path = control_dir / "failure.json"
        if not path.is_file():
            return None
        value = _read_protocol(path, "verdflow.model-api-worker.failure")
        message = value.get("message")
        return message if isinstance(message, str) and message.strip() else None


def _provider_document(provider: ModelApiProviderConfig) -> dict[str, Any]:
    descriptor = provider.descriptor
    capabilities = provider.capabilities
    return {
        "provider_id": descriptor.provider_id,
        "display_name": descriptor.display_name,
        "provider_version": descriptor.provider_version,
        "protocol": descriptor.protocol.value,
        "default_model": descriptor.default_model,
        "endpoint": provider.endpoint,
        "secret_ref": provider.secret_ref,
        "capabilities": {
            "context_window_tokens": capabilities.context_window_tokens,
            "max_output_tokens": capabilities.max_output_tokens,
            "default_max_output_tokens": capabilities.default_max_output_tokens,
            "reasoning_mode": capabilities.reasoning_mode.value,
        },
    }


def _worker_environment(api_key: str) -> dict[str, str]:
    allowed = ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "TEMP", "TMP")
    environment = {key: os.environ[key] for key in allowed if key in os.environ}
    import_paths = [value for value in sys.path if value and Path(value).exists()]
    if import_paths:
        environment["PYTHONPATH"] = os.pathsep.join(import_paths)
    environment["PYTHONIOENCODING"] = "utf-8"
    environment[MODEL_API_KEY_ENVIRONMENT] = api_key
    return environment


def _read_protocol(path: Path, schema: str) -> dict[str, Any]:
    value: Any = None
    last_error: Exception | None = None
    for attempt in range(20):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            break
        except (PermissionError, FileNotFoundError, json.JSONDecodeError) as error:
            last_error = error
            if attempt == 19:
                raise ModelApiWorkerError(
                    f"Worker protocol document was not readable: {path.name}"
                ) from error
            time.sleep(0.01)
    if last_error is not None and value is None:
        raise ModelApiWorkerError(
            f"Worker protocol document was not readable: {path.name}"
        ) from last_error
    if not isinstance(value, dict) or value.get("schema") != schema:
        raise ModelApiWorkerError(f"Invalid worker protocol document: {path.name}")
    if str(value.get("version")) != MODEL_API_WORKER_PROTOCOL_VERSION:
        raise ModelApiWorkerError(f"Unsupported worker protocol: {path.name}")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


def _required_text(value: dict[str, Any], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item.strip():
        raise ModelApiWorkerError(f"{field} is required")
    return item.strip()


def _now_text() -> str:
    return datetime.now(UTC).isoformat()
