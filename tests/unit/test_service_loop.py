from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
    str(ROOT / "packages" / "core" / "src"),
]

from verdflow.contracts import JobInfo, JobStatus  # noqa: E402
from verdflow.core import (  # noqa: E402
    ApplicationFacade,
    CommandDocument,
    JobRuntime,
    MaintenanceDescriptor,
    MaintenanceScheduler,
    MaintenanceTaskResult,
    ServiceLoop,
    ServiceLoopError,
    ServiceLoopState,
    WorkerDescriptor,
    WorkerRegistry,
)
from verdflow.project_store import ProjectStore  # noqa: E402


TEST_TEMP_ROOT = ROOT / "tests" / ".tmp"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)


@dataclass
class RuntimeDriver:
    runtime: JobRuntime
    descriptor: WorkerDescriptor = WorkerDescriptor(
        "runtime-driver",
        ("verdflow.test.complete",),
    )

    def run_once(self) -> JobInfo | None:
        claimed = self.runtime.claim_next(
            self.descriptor.worker_id,
            command_types=self.descriptor.command_types,
        )
        if claimed is None:
            return None
        return self.runtime.succeed(claimed.lease)

    def reconcile(self) -> int:
        return 0

    def request_cancel(self, job_id: str) -> None:
        return None


class FailingDriver(RuntimeDriver):
    def reconcile(self) -> int:
        raise RuntimeError("reconciliation failed")


class FailingMaintenanceTask:
    descriptor = MaintenanceDescriptor(
        "test.failing-maintenance",
        interval_seconds=60.0,
        retry_interval_seconds=30.0,
    )

    def run_once(self) -> MaintenanceTaskResult:
        raise RuntimeError("maintenance failed")


class ServiceLoopTests(unittest.TestCase):
    def test_unbounded_wait_still_runs_jobs_and_obeys_stop(self) -> None:
        with self._store() as store:
            runtime = JobRuntime(store)
            registry = WorkerRegistry()
            registry.register(RuntimeDriver(runtime))
            job = ApplicationFacade(store, runtime).submit_command(
                CommandDocument("verdflow.test.complete", "1", b'{"schema":"test"}'),
                idempotency_key="unbounded-wait",
            )
            loop = ServiceLoop(runtime, registry)
            loop.start()
            with patch(
                "verdflow.core.service_loop.time.monotonic", side_effect=[0.0, 3600.0],
            ) as clock:
                completed = loop.run_until_terminal(job.job_id, timeout_seconds=None)
                clock.assert_not_called()
            self.assertEqual(completed.status, JobStatus.SUCCEEDED)
            pending = ApplicationFacade(store, runtime).submit_command(
                CommandDocument("verdflow.test.complete", "1", b'{"schema":"test"}'),
                idempotency_key="stopped-unbounded-wait",
            )
            loop.stop()
            with self.assertRaisesRegex(ServiceLoopError, "stopped"):
                loop.run_until_terminal(pending.job_id, timeout_seconds=None)

    def test_explicit_wait_deadline_remains_enforced(self) -> None:
        with self._store() as store:
            runtime = JobRuntime(store)
            job = ApplicationFacade(store, runtime).submit_command(
                CommandDocument("verdflow.test.complete", "1", b'{"schema":"test"}'),
                idempotency_key="bounded-wait",
            )
            loop = ServiceLoop(runtime, WorkerRegistry())
            loop.start()
            with patch("verdflow.core.service_loop.time.monotonic", side_effect=[0.0, 3600.0]):
                with self.assertRaises(TimeoutError):
                    loop.run_until_terminal(job.job_id, timeout_seconds=1.0)
            loop.stop()

    def test_owner_thread_loop_recovers_then_completes_job(self) -> None:
        with self._store() as store:
            runtime = JobRuntime(store)
            registry = WorkerRegistry()
            registry.register(RuntimeDriver(runtime))
            facade = ApplicationFacade(store, runtime, cancellation_port=registry)
            job = facade.submit_command(
                CommandDocument(
                    "verdflow.test.complete",
                    "1",
                    b'{"schema":"test"}',
                ),
                idempotency_key="service-loop-complete",
            )
            loop = ServiceLoop(runtime, registry, poll_interval_seconds=0.01)

            report = loop.start()
            completed = loop.run_until_terminal(job.job_id)
            loop.stop()

            self.assertEqual(completed.status, JobStatus.SUCCEEDED)
            self.assertEqual(loop.state, ServiceLoopState.STOPPED)
            self.assertEqual(
                report.job_recovery.interrupted_count,
                0,
            )

    def test_start_surfaces_reconciliation_failure(self) -> None:
        with self._store() as store:
            runtime = JobRuntime(store)
            registry = WorkerRegistry()
            registry.register(FailingDriver(runtime))
            loop = ServiceLoop(runtime, registry)

            with self.assertRaises(ServiceLoopError):
                loop.start()

            self.assertEqual(loop.state, ServiceLoopState.FAILED)

    def test_maintenance_failure_does_not_fail_worker_cycle(self) -> None:
        with self._store() as store:
            runtime = JobRuntime(store)
            registry = WorkerRegistry()
            registry.register(RuntimeDriver(runtime))
            facade = ApplicationFacade(store, runtime, cancellation_port=registry)
            job = facade.submit_command(
                CommandDocument(
                    "verdflow.test.complete",
                    "1",
                    b'{"schema":"test"}',
                ),
                idempotency_key="maintenance-failure",
            )
            maintenance = MaintenanceScheduler()
            maintenance.register(FailingMaintenanceTask())
            loop = ServiceLoop(
                runtime,
                registry,
                maintenance_scheduler=maintenance,
            )
            loop.start()

            cycle = loop.run_once()

            self.assertEqual(cycle.completed_jobs[0].job_id, job.job_id)
            self.assertEqual(runtime.get(job.job_id).status, JobStatus.SUCCEEDED)
            self.assertEqual(loop.state, ServiceLoopState.RUNNING)
            self.assertEqual(len(cycle.maintenance_executions), 1)
            self.assertFalse(cycle.maintenance_executions[0].succeeded)

    def _store(self) -> ProjectStore:
        temporary = tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT)
        self.addCleanup(temporary.cleanup)
        return ProjectStore.create(Path(temporary.name) / "test.verdflow", "Test")


if __name__ == "__main__":
    unittest.main()
