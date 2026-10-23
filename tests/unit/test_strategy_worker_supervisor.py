from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sys
import tempfile
from threading import Thread
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / path) for path in (
    "packages/contracts/src", "packages/worker-runtime/src",
    "packages/project-store/src", "packages/planting/src", "packages/core/src",
    "adapters/strategy-worker/src",
)]

from verdflow.contracts import JobCommandRef, JobEventType, JobStatus  # noqa: E402
from verdflow.core import JobRuntime  # noqa: E402
from verdflow.project_store import ProjectStore  # noqa: E402
from verdflow.strategy_worker import (  # noqa: E402
    StrategyExecutionCancelled,
    StrategyWorkerSupervisor,
    StrategyWorkerSupervisorConfig,
    inspect_strategy_package,
)
from verdflow.strategy_worker.protocol import STRATEGY_GENERATE_POINTS_COMMAND  # noqa: E402


@contextmanager
def supervisor_fixture():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        with ProjectStore.create(root / "project", "Cancel supervisor") as store:
            runtime = JobRuntime(store)
            command = store.put_bytes(b"{}", media_type="application/json")
            job = runtime.submit(
                JobCommandRef(STRATEGY_GENERATE_POINTS_COMMAND, "1", command.artifact_id),
                idempotency_key="cancel-test",
            )
            package = inspect_strategy_package(ROOT / "strategy-packs/universal-street-tree")
            authorizer = Mock()
            authorizer.authorize.return_value = SimpleNamespace(
                strategy=SimpleNamespace(package_hash=package.package_hash),
            )
            resolver = Mock()
            resolver.strategy_package_root.return_value = package.root
            supervisor = StrategyWorkerSupervisor(
                runtime, authorizer, resolver,
                StrategyWorkerSupervisorConfig(runtime_root=root / "runtime"),
            )
            with (
                patch.object(supervisor, "_load_command", return_value={
                    "strategy": {"id": package.package_id, "version": package.package_version,
                                 "package_hash": package.package_hash},
                    "random_seed": 17, "args": {},
                }),
                patch.object(supervisor, "_selected_regions", return_value=[]),
            ):
                yield supervisor, runtime, job


class StrategyWorkerSupervisorTests(unittest.TestCase):
    def assert_cancelled_without_recovery(self, runtime, job_id):
        self.assertEqual(runtime.get(job_id).status, JobStatus.CANCELLED)
        self.assertEqual(runtime.attempts(job_id)[0].status, JobStatus.CANCELLED)
        events = runtime.events(job_id)
        self.assertEqual([event.event_type for event in events][-2:], [
            JobEventType.CANCELLATION_REQUESTED, JobEventType.CANCELLED,
        ])
        self.assertIsNone(events[-1].message)
        self.assertIsNone(runtime.get(job_id).result_artifact_id)

    def test_control_signal_cancels_real_process_before_owner_mailbox_write(self):
        with supervisor_fixture() as (supervisor, runtime, job):
            signal = Thread(target=supervisor.request_cancel, args=(job.job_id,))
            signal.start()
            signal.join(1.0)
            self.assertFalse(signal.is_alive())
            result = supervisor.run_once()
            self.assertEqual(result.status, JobStatus.CANCELLED)
            self.assert_cancelled_without_recovery(runtime, job.job_id)
            self.assertEqual(runtime.store.list_active_worker_processes(), ())
            self.assertEqual(list(supervisor.config.runtime_root.iterdir()), [])

    def test_control_signal_after_process_exit_discards_output(self):
        with supervisor_fixture() as (supervisor, runtime, job):
            def finished(*args, **kwargs):
                supervisor.request_cancel(job.job_id)
                return {}
            with patch("verdflow.strategy_worker.supervisor.StrategyWorker.run", side_effect=finished):
                result = supervisor.run_once()
            self.assertEqual(result.status, JobStatus.CANCELLED)
            self.assert_cancelled_without_recovery(runtime, job.job_id)

    def test_already_persisted_cancellation_remains_idempotent(self):
        with supervisor_fixture() as (supervisor, runtime, job):
            def cancelled(*args, **kwargs):
                runtime.request_cancel(job.job_id)
                supervisor.request_cancel(job.job_id)
                raise StrategyExecutionCancelled("test signal")
            with patch("verdflow.strategy_worker.supervisor.StrategyWorker.run", side_effect=cancelled):
                supervisor.run_once()
            self.assert_cancelled_without_recovery(runtime, job.job_id)
            self.assertEqual(sum(event.event_type == JobEventType.CANCELLATION_REQUESTED
                                 for event in runtime.events(job.job_id)), 1)

    def test_genuinely_expired_lease_remains_fenced_without_crashing_owner(self):
        with supervisor_fixture() as (supervisor, runtime, job):
            now = [datetime.now(UTC)]
            runtime.clock = Mock()
            runtime.clock.now.side_effect = lambda: now[0]
            def expired(*args, **kwargs):
                now[0] += runtime.lease_duration + timedelta(seconds=1)
                supervisor.request_cancel(job.job_id)
                raise StrategyExecutionCancelled("test signal after lease expiry")
            with patch("verdflow.strategy_worker.supervisor.StrategyWorker.run", side_effect=expired):
                self.assertIsNone(supervisor.run_once())
            self.assertEqual(runtime.get(job.job_id).status, JobStatus.CANCELLING)
            runtime.recover_expired()
            self.assertEqual(runtime.get(job.job_id).status, JobStatus.CANCELLED)


if __name__ == "__main__":
    unittest.main()
