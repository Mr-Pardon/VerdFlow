"""Drive trusted strategy Jobs while Core owns durable state."""

from __future__ import annotations

import json
import shutil
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Protocol

from verdflow.contracts import (
    JobErrorInfo,
    JobInfo,
    JobLease,
    WorkerProcessInfo,
    WorkerProcessStatus,
)
from verdflow.core import JobRuntime, StrategyAccessError, StrategyExecutionAuthorizer
from verdflow.planting import canonical_json_bytes
from verdflow.project_store import JobLeaseConflictError, ProcessStatus, SystemProcessInspector
from verdflow.worker_runtime import terminate_process_by_pid

from .errors import StrategyExecutionCancelled, StrategyWorkerError
from .package import inspect_strategy_package
from .package_archive import package_archive_bytes
from .protocol import (
    STRATEGY_COMMAND_SCHEMA,
    STRATEGY_COMMAND_VERSION,
    STRATEGY_SUPPORTED_COMMANDS,
    STRATEGY_WORKER_ADAPTER_ID,
    StrategyWorkerSupervisorConfig,
)
from .runner import StrategyWorker


class StrategyPackageResolver(Protocol):
    def strategy_package_root(self, package_hash: str) -> Path: ...


class StrategyWorkerSupervisor:
    """Claim, authorize and execute one strategy generation Job."""

    def __init__(
        self,
        runtime: JobRuntime,
        authorizer: StrategyExecutionAuthorizer,
        package_resolver: StrategyPackageResolver,
        config: StrategyWorkerSupervisorConfig | None = None,
        *,
        worker_id: str | None = None,
        process_started: Callable[[WorkerProcessInfo], None] | None = None,
    ) -> None:
        self.runtime = runtime
        self.authorizer = authorizer
        self.package_resolver = package_resolver
        self.config = config or StrategyWorkerSupervisorConfig()
        self.config.validate()
        self.worker_id = worker_id or f"strategy_{uuid.uuid4().hex}"
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
            command_types=STRATEGY_SUPPORTED_COMMANDS,
        )
        if claimed is None:
            return None
        lease = claimed.lease
        process_registered = False

        def register_process(pid: int) -> None:
            nonlocal process_registered
            identity = self._child_identity(pid)
            record = WorkerProcessInfo(
                job_id=lease.job_id,
                attempt_number=lease.attempt_count,
                adapter_id=STRATEGY_WORKER_ADAPTER_ID,
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
            process_registered = True
            if self._process_started is not None:
                self._process_started(record)

        def finish_process(exit_code: int | None, terminated: bool) -> None:
            if not process_registered:
                return
            self.runtime.store.finish_worker_process(
                lease.job_id,
                lease.attempt_count,
                status=(
                    WorkerProcessStatus.TERMINATED
                    if terminated
                    else WorkerProcessStatus.EXITED
                ),
                finished_at=_now_text(),
                exit_code=exit_code,
            )

        try:
            command = self._load_command(claimed.job.command.artifact_id)
            self.runtime.heartbeat(
                lease,
                progress=0.05,
                stage="authorizing_strategy",
            )
            strategy_ref = self._strategy_ref(command)
            authorization = self.authorizer.authorize(
                strategy_ref["package_hash"]
            )
            package_root = self.package_resolver.strategy_package_root(
                strategy_ref["package_hash"]
            )
            package = inspect_strategy_package(package_root)
            if (
                package.package_id != strategy_ref["id"]
                or package.package_version != strategy_ref["version"]
                or package.package_hash != strategy_ref["package_hash"]
                or authorization.strategy.package_hash != package.package_hash
            ):
                raise ValueError(
                    "Strategy package identity differs from the Job command"
                )
            selected_regions = self._selected_regions(command)
            package_archive = package_archive_bytes(package)
            self.runtime.heartbeat(
                lease,
                progress=0.15,
                stage="running_strategy",
            )
            last_heartbeat = time.monotonic()

            def heartbeat() -> None:
                nonlocal last_heartbeat
                now = time.monotonic()
                if now - last_heartbeat >= self.config.heartbeat_interval_seconds:
                    self.runtime.heartbeat(
                        lease,
                        progress=0.5,
                        stage="running_strategy",
                    )
                    if process_registered:
                        self.runtime.store.heartbeat_worker_process(
                            lease.job_id,
                            lease.attempt_count,
                            _now_text(),
                        )
                    last_heartbeat = now

            points = StrategyWorker(
                {package.package_hash},
                self.config.worker,
            ).run(
                package,
                {
                    "strategy": package.strategy,
                    "selected_regions": selected_regions,
                    "seed": command["random_seed"],
                    "args": command["args"],
                    "excluded_role_keys": command.get("excluded_role_keys", []),
                },
                heartbeat=heartbeat,
                cancellation_requested=lambda: self._is_cancelled(
                    lease.job_id
                ),
                process_started=register_process,
                process_finished=finish_process,
                workdir_parent=self.config.runtime_root,
                workdir_prefix=(
                    f"{lease.job_id}-{lease.attempt_count}-"
                ),
            )
            if self._is_cancelled(lease.job_id):
                return self._acknowledge_cancel(lease)
            self.runtime.heartbeat(
                lease,
                progress=0.9,
                stage="persisting_strategy_result",
            )
            point_artifact = self.runtime.store.put_bytes(
                canonical_json_bytes(points),
                media_type="application/vnd.verdflow.points+json",
                schema_id="verdflow.points",
                schema_version="2",
            )
            package_artifact = self.runtime.store.put_bytes(
                package_archive,
                media_type="application/vnd.verdflow.strategy-package+zip",
                schema_id="verdflow.strategy-package",
                schema_version="1",
            )
            result = {
                "schema": "verdflow.strategy-worker.result",
                "version": "1",
                "result_kind": "planting_generation",
                "producer": {
                    "id": package.package_id,
                    "version": package.package_version,
                },
                "output_artifacts": [
                    {"role": "points", "artifact_id": point_artifact.artifact_id},
                    {
                        "role": "strategy_package",
                        "artifact_id": package_artifact.artifact_id,
                    },
                ],
            }
            result_artifact = self.runtime.store.put_bytes(
                canonical_json_bytes(result),
                media_type="application/vnd.verdflow.worker-result+json",
                schema_id="verdflow.strategy-worker.result",
                schema_version="1",
            )
            return self.runtime.succeed(
                lease,
                result_artifact_id=result_artifact.artifact_id,
            )
        except StrategyExecutionCancelled:
            return self._acknowledge_cancel(lease)
        except StrategyAccessError as error:
            return self.runtime.fail(
                lease,
                JobErrorInfo(error.code, str(error), retryable=False),
            )
        except (StrategyWorkerError, ValueError, KeyError, OSError) as error:
            return self.runtime.fail(
                lease,
                JobErrorInfo(
                    "STRATEGY_EXECUTION_FAILED",
                    str(error),
                    retryable=False,
                ),
            )
        finally:
            with self._cancel_lock:
                self._cancel_requests.discard(lease.job_id)

    def reconcile_recorded_processes(self) -> int:
        """Converge Strategy process records left by a previous Host."""
        inspector = SystemProcessInspector()
        recovered = 0
        for record in self.runtime.store.list_active_worker_processes():
            if record.adapter_id != STRATEGY_WORKER_ADAPTER_ID:
                continue
            probe = inspector.probe(record.host, record.pid)
            if probe.status is ProcessStatus.UNKNOWN:
                raise StrategyWorkerError(
                    "Recorded Strategy process identity cannot be inspected: "
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
            self._remove_attempt_runtime_dirs(
                record.job_id,
                record.attempt_number,
            )
            recovered += 1
        return recovered

    def _load_command(self, artifact_id: str) -> dict[str, Any]:
        with self.runtime.store.open_artifact(artifact_id) as stream:
            value = json.load(stream)
        if not isinstance(value, dict):
            raise ValueError("Strategy command must be an object")
        if (
            value.get("schema") != STRATEGY_COMMAND_SCHEMA
            or str(value.get("version")) != STRATEGY_COMMAND_VERSION
            or value.get("operation") != "generate_points"
        ):
            raise ValueError("Strategy command schema is unsupported")
        if not isinstance(value.get("args"), dict):
            raise ValueError("Strategy command args must be an object")
        seed = value.get("random_seed")
        if not isinstance(seed, int) or isinstance(seed, bool):
            raise ValueError("Strategy command random_seed must be an integer")
        excluded_roles = value.get("excluded_role_keys", [])
        if (
            not isinstance(excluded_roles, list)
            or not all(isinstance(role, str) and role for role in excluded_roles)
            or len(set(excluded_roles)) != len(excluded_roles)
        ):
            raise ValueError("excluded_role_keys must contain unique role keys")
        return value

    def _strategy_ref(self, command: dict[str, Any]) -> dict[str, str]:
        strategy = command.get("strategy")
        if not isinstance(strategy, dict):
            raise ValueError("Strategy command reference is required")
        return {
            field: self._required_text(strategy, field)
            for field in ("id", "version", "package_hash")
        }

    def _selected_regions(self, command: dict[str, Any]) -> list[dict[str, Any]]:
        region_set_id = self._required_text(command, "region_set_id")
        scene_revision_id = self._required_text(command, "scene_revision_id")
        region_set = self.runtime.store.get_region_set(region_set_id)
        if region_set.scene_revision_id != scene_revision_id:
            raise ValueError("Strategy command RegionSet differs from SceneRevision")
        selected_ids = command.get("selected_region_ids")
        if (
            not isinstance(selected_ids, list)
            or not selected_ids
            or not all(isinstance(value, str) and value for value in selected_ids)
            or len(set(selected_ids)) != len(selected_ids)
        ):
            raise ValueError("selected_region_ids must contain unique Region ids")
        with self.runtime.store.open_artifact(
            region_set.geometry_artifact_id
        ) as stream:
            geometry = json.load(stream)
        if not isinstance(geometry, dict):
            raise ValueError("Region geometry must be an object")
        if (
            geometry.get("schema") != "verdflow.regions"
            or str(geometry.get("version")) != "1"
            or geometry.get("coordinate_space") != "source_scene_world"
            or geometry.get("offset_applied") is not False
        ):
            raise ValueError("Region geometry contract is unsupported")
        rows = geometry.get("regions")
        if not isinstance(rows, list):
            raise ValueError("Region geometry rows are required")
        by_id: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Region geometry row must be an object")
            region_id = self._required_text(row, "id")
            if region_id in by_id:
                raise ValueError(f"Region geometry is duplicated: {region_id}")
            by_id[region_id] = row
        missing = [region_id for region_id in selected_ids if region_id not in by_id]
        if missing:
            raise ValueError(
                "Selected Region geometry is missing: " + ", ".join(missing[:3])
            )
        return [by_id[region_id] for region_id in selected_ids]

    def _is_cancelled(self, job_id: str) -> bool:
        with self._cancel_lock:
            return job_id in self._cancel_requests

    def _acknowledge_cancel(self, lease: JobLease) -> JobInfo | None:
        # The control signal can arrive before the owner mailbox persists intent.
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
            raise StrategyWorkerError(
                "Could not establish a stable Strategy process identity"
            )
        return probe.identity

    def _remove_attempt_runtime_dirs(
        self,
        job_id: str,
        attempt_number: int,
    ) -> None:
        prefix = f"{job_id}-{attempt_number}-"
        for path in self.config.runtime_root.iterdir():
            if path.is_dir() and path.name.startswith(prefix):
                shutil.rmtree(path, ignore_errors=True)

    @staticmethod
    def _required_text(value: dict[str, Any], field: str) -> str:
        item = value.get(field)
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{field} is required")
        return item.strip()


def _now_text() -> str:
    return datetime.now(UTC).isoformat()
