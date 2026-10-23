"""Single-owner recoverable worker loop for one VerdFlow project."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from verdflow.contracts import JobInfo, JobRecoveryResult, JobStatus

from .job_runtime import JobRuntime
from .maintenance import MaintenanceExecution, MaintenanceScheduler
from .workers import WorkerReconciliation, WorkerRegistry


class ServiceLoopState(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ServiceStartupReport:
    worker_reconciliation: tuple[WorkerReconciliation, ...]
    job_recovery: JobRecoveryResult


@dataclass(frozen=True, slots=True)
class ServiceCycleResult:
    completed_jobs: tuple[JobInfo, ...]
    job_recovery: JobRecoveryResult
    maintenance_executions: tuple[MaintenanceExecution, ...] = ()
    application_task_count: int = 0

    @property
    def did_work(self) -> bool:
        return bool(
            self.completed_jobs
            or self.application_task_count
            or self.maintenance_executions
            or self.job_recovery.interrupted_count
            or self.job_recovery.requeued_count
            or self.job_recovery.cancelled_count
        )


class ServiceCycleTask(Protocol):
    """One bounded owner-thread application task."""

    def run_once(self) -> bool: ...


class ServiceLoopError(RuntimeError):
    """Raised when the project-owner loop cannot execute safely."""


TERMINAL_JOB_STATUSES = {
    JobStatus.CANCELLED,
    JobStatus.FAILED,
    JobStatus.INTERRUPTED,
    JobStatus.SUCCEEDED,
}


class ServiceLoop:
    """Drive workers on the same thread that owns the ProjectStore connection."""

    def __init__(
        self,
        runtime: JobRuntime,
        registry: WorkerRegistry,
        *,
        poll_interval_seconds: float = 0.1,
        maintenance_scheduler: MaintenanceScheduler | None = None,
        application_tasks: tuple[ServiceCycleTask, ...] = (),
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        self.runtime = runtime
        self.registry = registry
        self.poll_interval_seconds = poll_interval_seconds
        self.maintenance_scheduler = maintenance_scheduler
        self.application_tasks = application_tasks
        self._state = ServiceLoopState.STOPPED
        self._owner_thread_id: int | None = None
        self._stop_requested = threading.Event()
        self._failure: Exception | None = None
        self._startup_report: ServiceStartupReport | None = None

    @property
    def state(self) -> ServiceLoopState:
        return self._state

    @property
    def startup_report(self) -> ServiceStartupReport | None:
        return self._startup_report

    def start(self) -> ServiceStartupReport:
        if self._state is not ServiceLoopState.STOPPED:
            raise RuntimeError(f"ServiceLoop cannot start from {self._state}")
        self._state = ServiceLoopState.STARTING
        self._owner_thread_id = threading.get_ident()
        self._stop_requested.clear()
        self.registry.seal()
        try:
            report = ServiceStartupReport(
                worker_reconciliation=self.registry.reconcile_all(),
                job_recovery=self.runtime.recover_expired(),
            )
            if self.maintenance_scheduler is not None:
                self.maintenance_scheduler.start()
        except Exception as error:
            self._failure = error
            self._state = ServiceLoopState.FAILED
            raise ServiceLoopError("ServiceLoop startup recovery failed") from error
        self._startup_report = report
        self._state = ServiceLoopState.RUNNING
        return report

    def run_once(self) -> ServiceCycleResult:
        self._require_owner_thread()
        if self._state is not ServiceLoopState.RUNNING:
            raise RuntimeError(f"ServiceLoop cannot run from {self._state}")
        try:
            completed_jobs = self.registry.run_once()
            application_task_count = sum(
                1 for task in self.application_tasks if task.run_once()
            )
            return ServiceCycleResult(
                completed_jobs=completed_jobs,
                job_recovery=self.runtime.recover_expired(),
                maintenance_executions=(
                    self.maintenance_scheduler.run_due()
                    if self.maintenance_scheduler is not None
                    else ()
                ),
                application_task_count=application_task_count,
            )
        except Exception as error:
            self._failure = error
            self._state = ServiceLoopState.FAILED
            raise ServiceLoopError("ServiceLoop cycle failed") from error

    def run_until_terminal(
        self,
        job_id: str,
        *,
        timeout_seconds: float | None = 600.0,
    ) -> JobInfo:
        self._require_owner_thread()
        if timeout_seconds is not None and (
            not math.isfinite(timeout_seconds) or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be finite and positive, or None")
        deadline = (
            time.monotonic() + timeout_seconds
            if timeout_seconds is not None
            else None
        )
        while True:
            job = self.runtime.get(job_id)
            if job.status in TERMINAL_JOB_STATUSES:
                return job
            if self._stop_requested.is_set():
                raise ServiceLoopError("ServiceLoop stopped before Job completed")
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError(f"Job did not complete in time: {job_id}")
            cycle = self.run_once()
            if not cycle.did_work:
                self._stop_requested.wait(self.poll_interval_seconds)

    def serve_forever(self) -> None:
        self._require_owner_thread()
        while not self._stop_requested.is_set():
            cycle = self.run_once()
            if not cycle.did_work:
                self._stop_requested.wait(self.poll_interval_seconds)
        if self._state is not ServiceLoopState.FAILED:
            self._state = ServiceLoopState.STOPPED

    def stop(self) -> None:
        if self._state in {ServiceLoopState.STOPPED, ServiceLoopState.FAILED}:
            return
        self._state = ServiceLoopState.STOPPING
        self._stop_requested.set()
        if threading.get_ident() == self._owner_thread_id:
            self._state = ServiceLoopState.STOPPED

    def raise_if_failed(self) -> None:
        if self._failure is not None:
            raise ServiceLoopError("ServiceLoop failed") from self._failure

    def _require_owner_thread(self) -> None:
        if self._owner_thread_id is None:
            raise RuntimeError("ServiceLoop has not started")
        if threading.get_ident() != self._owner_thread_id:
            raise ServiceLoopError(
                "ServiceLoop must run on the ProjectStore owner thread"
            )
