"""Durable Core worker for user-confirmed subsets of immutable Region results."""

from __future__ import annotations

import errno
import sqlite3
from time import monotonic
from typing import Generator

from verdflow.contracts import JobErrorInfo, JobInfo, JobStatus
from verdflow.project_store import JobLeaseConflictError

from .job_runtime import JobRuntime
from .region_source_import import REGION_SOURCE_CONFIRM_COMMAND, RegionSelectionRefiner, RegionSourceImportService
from .workers import WorkerDescriptor, WorkerJobPoller


class _ConfirmationCancelled(Exception):
    pass


class RegionConfirmationWorker:
    descriptor = WorkerDescriptor("core-region-confirmation", (REGION_SOURCE_CONFIRM_COMMAND,))

    def __init__(self, runtime: JobRuntime, refiner: RegionSelectionRefiner | None = None) -> None:
        self.runtime = runtime
        self._service = RegionSourceImportService(runtime.store)
        self._cancel_requests: set[str] = set()
        self._refiner = refiner
        self._poller = WorkerJobPoller(self._run_next)

    def reconcile(self) -> int:
        return 0

    def request_cancel(self, job_id: str) -> None:
        self._cancel_requests.add(job_id)

    def run_once(self) -> JobInfo | None:
        return self._poller.poll()

    @property
    def active(self) -> bool:
        return self._poller.active

    def close(self) -> None:
        self._poller.close()

    def _run_next(self) -> Generator[None, None, JobInfo | None]:
        claimed = self.runtime.claim_next(self.descriptor.worker_id, command_types=self.descriptor.command_types)
        if claimed is None:
            return None
        lease = claimed.lease
        try:
            self.runtime.heartbeat(lease, progress=0.1, stage="filtering_region_selection")
            last = monotonic()
            interval = min(5.0, self.runtime.lease_duration.total_seconds() / 3)

            def check_progress() -> None:
                nonlocal last
                if lease.job_id in self._cancel_requests:
                    raise _ConfirmationCancelled()
                now = monotonic()
                if now - last >= interval:
                    # A revision marks its producer succeeded inside the same publication transaction.
                    if self.runtime.get(lease.job_id).status is JobStatus.RUNNING:
                        self.runtime.heartbeat(lease)
                    last = now

            check_progress()
            return (yield from self._service.run_confirmation(self.runtime, lease, check_progress, self._refiner))
        except JobLeaseConflictError:
            return None
        except _ConfirmationCancelled:
            try:
                self.runtime.request_cancel(lease.job_id)
                return self.runtime.acknowledge_cancel(lease)
            except JobLeaseConflictError:
                return None
        except (RuntimeError, ValueError, KeyError, OSError, sqlite3.Error, MemoryError) as error:
            if getattr(error, "errno", None) == errno.ENOSPC or getattr(error, "sqlite_errorcode", None) == sqlite3.SQLITE_FULL:
                message = "项目磁盘空间不足，未确认区域。原始候选已保留，释放空间后可重试。"
            elif isinstance(error, MemoryError):
                message = "确认区域时内存不足。原始候选已保留，可释放内存或缩小选择后重试。"
            else:
                message = str(error) or "未能确认区域，原始候选已保留。"
            try:
                return self.runtime.fail(lease, JobErrorInfo("REGION_CONFIRMATION_FAILED", message, retryable=False))
            except JobLeaseConflictError:
                return None
        finally:
            self._cancel_requests.discard(lease.job_id)
