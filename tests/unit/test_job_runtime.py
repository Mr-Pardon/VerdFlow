from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "packages" / "contracts" / "src"),
    str(ROOT / "packages" / "project-store" / "src"),
    str(ROOT / "packages" / "core" / "src"),
]

from verdflow.contracts import (  # noqa: E402
    JobCommandRef,
    JobErrorInfo,
    JobEventType,
    JobRetentionClass,
    JobStatus,
)
from verdflow.core import JobRetentionPolicy, JobRuntime  # noqa: E402
from verdflow.project_store import (  # noqa: E402
    JobConflictError,
    JobIdempotencyConflictError,
    JobLeaseConflictError,
    ProjectStore,
)


TEST_TEMP_ROOT = ROOT / "tests" / ".tmp"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)


@dataclass
class ManualClock:
    value: datetime

    def now(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


def command_ref(store: ProjectStore, payload: bytes = b"command") -> JobCommandRef:
    artifact = store.put_bytes(
        payload,
        media_type="application/vnd.verdflow.command+json",
        schema_id="verdflow.test-command",
        schema_version="1",
    )
    return JobCommandRef(
        command_type="test.echo",
        command_version="1",
        artifact_id=artifact.artifact_id,
    )


class JobRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.initial_time = datetime(2026, 7, 29, 8, 0, tzinfo=UTC)

    def test_submission_is_idempotent_but_rejects_key_reuse(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            path = Path(directory) / "idempotency.verdflow"
            with ProjectStore.create(path, "Idempotency") as store:
                runtime = JobRuntime(store, clock=ManualClock(self.initial_time))
                first_command = command_ref(store, b"first")
                first = runtime.submit(
                    first_command,
                    idempotency_key="request-1",
                    max_attempts=2,
                )
                repeated = runtime.submit(
                    first_command,
                    idempotency_key="request-1",
                    max_attempts=2,
                )
                self.assertEqual(first.job_id, repeated.job_id)

                with self.assertRaises(JobIdempotencyConflictError):
                    runtime.submit(
                        command_ref(store, b"second"),
                        idempotency_key="request-1",
                        max_attempts=2,
                    )

    def test_candidate_retention_is_frozen_then_promoted(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            path = Path(directory) / "candidate-retention.verdflow"
            with ProjectStore.create(path, "Candidate retention") as store:
                clock = ManualClock(self.initial_time)
                runtime = JobRuntime(
                    store,
                    clock=clock,
                    retention_policy=JobRetentionPolicy(
                        temporary=timedelta(seconds=10),
                        candidate=timedelta(seconds=20),
                    ),
                )
                job = runtime.submit(
                    command_ref(store),
                    idempotency_key="candidate-retention",
                    retention_class=JobRetentionClass.CANDIDATE,
                )
                self.assertEqual(
                    job.retention_class,
                    JobRetentionClass.CANDIDATE,
                )
                self.assertEqual(job.retention_seconds, 20)
                self.assertIsNone(job.purge_after)

                claimed = runtime.claim_next("worker-a")
                assert claimed is not None
                completed = runtime.succeed(claimed.lease)
                self.assertEqual(
                    completed.purge_after,
                    (self.initial_time + timedelta(seconds=20)).isoformat(),
                )

                promoted = runtime.promote(job.job_id)
                self.assertEqual(promoted.retention_class, JobRetentionClass.RUN)
                self.assertIsNone(promoted.retention_seconds)
                self.assertIsNone(promoted.purge_after)
                self.assertEqual(runtime.promote(job.job_id), promoted)
                with self.assertRaises(JobConflictError):
                    runtime.promote(
                        job.job_id,
                        retention_class=JobRetentionClass.AUDIT,
                    )

    def test_worker_claim_can_filter_supported_command_types(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            path = Path(directory) / "command-filter.verdflow"
            with ProjectStore.create(path, "Command filter") as store:
                runtime = JobRuntime(store, clock=ManualClock(self.initial_time))
                first = runtime.submit(
                    JobCommandRef(
                        command_type="agent.train",
                        command_version="1",
                        artifact_id=command_ref(store, b"agent").artifact_id,
                    ),
                    idempotency_key="agent",
                )
                second = runtime.submit(
                    JobCommandRef(
                        command_type="blender.probe",
                        command_version="1",
                        artifact_id=command_ref(store, b"blender").artifact_id,
                    ),
                    idempotency_key="blender",
                )

                claimed = runtime.claim_next(
                    "blender-worker",
                    command_types=("blender.probe",),
                )

                assert claimed is not None
                self.assertEqual(claimed.job.job_id, second.job_id)
                self.assertEqual(runtime.get(first.job_id).status, JobStatus.QUEUED)

    def test_heartbeat_events_are_sparse_and_replayable(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            path = Path(directory) / "events.verdflow"
            with ProjectStore.create(path, "Events") as store:
                clock = ManualClock(self.initial_time)
                runtime = JobRuntime(
                    store,
                    clock=clock,
                    lease_duration=timedelta(seconds=10),
                )
                job = runtime.submit(
                    command_ref(store),
                    idempotency_key="events-1",
                )
                claimed = runtime.claim_next("worker-a")
                self.assertIsNotNone(claimed)
                assert claimed is not None

                clock.advance(timedelta(seconds=1))
                runtime.heartbeat(claimed.lease)
                self.assertEqual(len(runtime.events(job.job_id)), 2)

                clock.advance(timedelta(seconds=1))
                runtime.heartbeat(
                    claimed.lease,
                    progress=0.4,
                    stage="extracting",
                )
                result = store.put_bytes(
                    b"result",
                    media_type="application/octet-stream",
                )
                completed = runtime.succeed(
                    claimed.lease,
                    result_artifact_id=result.artifact_id,
                )

                self.assertEqual(completed.status, JobStatus.SUCCEEDED)
                self.assertEqual(completed.progress, 1.0)
                replay = runtime.events(job.job_id, after_sequence=2)
                self.assertEqual(
                    [event.event_type for event in replay],
                    [JobEventType.PROGRESS, JobEventType.SUCCEEDED],
                )
                self.assertEqual(
                    [event.sequence for event in replay],
                    [3, 4],
                )

    def test_heartbeat_can_extend_one_long_running_lease(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            path = Path(directory) / "extended-lease.verdflow"
            with ProjectStore.create(path, "Extended lease") as store:
                clock = ManualClock(self.initial_time)
                runtime = JobRuntime(
                    store,
                    clock=clock,
                    lease_duration=timedelta(seconds=5),
                )
                job = runtime.submit(
                    command_ref(store),
                    idempotency_key="extended-lease",
                )
                claimed = runtime.claim_next("worker-a")
                assert claimed is not None

                clock.advance(timedelta(seconds=1))
                runtime.heartbeat(
                    claimed.lease,
                    lease_duration=timedelta(minutes=2),
                )
                clock.advance(timedelta(seconds=30))

                self.assertEqual(runtime.recover_expired().interrupted_count, 0)
                self.assertEqual(
                    runtime.get(job.job_id).status,
                    JobStatus.RUNNING,
                )
                self.assertEqual(
                    runtime.succeed(claimed.lease).status,
                    JobStatus.SUCCEEDED,
                )

    def test_expired_lease_requeues_and_rejects_late_worker_result(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            path = Path(directory) / "retry.verdflow"
            with ProjectStore.create(path, "Retry") as store:
                clock = ManualClock(self.initial_time)
                runtime = JobRuntime(
                    store,
                    clock=clock,
                    lease_duration=timedelta(seconds=5),
                )
                job = runtime.submit(
                    command_ref(store),
                    idempotency_key="retry-1",
                    max_attempts=2,
                )
                first = runtime.claim_next("worker-old")
                assert first is not None
                runtime.heartbeat(
                    first.lease,
                    progress=0.75,
                    stage="first_attempt_work",
                )
                clock.advance(timedelta(seconds=6))

                recovery = runtime.recover_expired()
                self.assertEqual(recovery.interrupted_count, 1)
                self.assertEqual(recovery.requeued_count, 1)
                self.assertEqual(runtime.get(job.job_id).status, JobStatus.QUEUED)
                self.assertEqual(runtime.get(job.job_id).progress, 0.0)

                second = runtime.claim_next("worker-new")
                assert second is not None
                self.assertEqual(second.lease.attempt_count, 2)
                retried = runtime.heartbeat(
                    second.lease,
                    progress=0.1,
                    stage="second_attempt_work",
                )
                self.assertEqual(retried.progress, 0.1)
                with self.assertRaises(JobLeaseConflictError):
                    runtime.succeed(first.lease)

                completed = runtime.succeed(second.lease)
                self.assertEqual(completed.status, JobStatus.SUCCEEDED)
                attempts = runtime.attempts(job.job_id)
                self.assertEqual(
                    [attempt.worker_id for attempt in attempts],
                    ["worker-old", "worker-new"],
                )
                self.assertEqual(
                    [attempt.status for attempt in attempts],
                    [JobStatus.INTERRUPTED, JobStatus.SUCCEEDED],
                )
                self.assertEqual(
                    [event.status for event in runtime.events(job.job_id)],
                    [
                        JobStatus.QUEUED,
                        JobStatus.RUNNING,
                        JobStatus.RUNNING,
                        JobStatus.INTERRUPTED,
                        JobStatus.QUEUED,
                        JobStatus.RUNNING,
                        JobStatus.RUNNING,
                        JobStatus.SUCCEEDED,
                    ],
                )

    def test_exhausted_attempt_remains_interrupted(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            path = Path(directory) / "interrupted.verdflow"
            with ProjectStore.create(path, "Interrupted") as store:
                clock = ManualClock(self.initial_time)
                runtime = JobRuntime(
                    store,
                    clock=clock,
                    lease_duration=timedelta(seconds=5),
                )
                job = runtime.submit(
                    command_ref(store),
                    idempotency_key="interrupted-1",
                )
                claimed = runtime.claim_next("worker-a")
                assert claimed is not None
                clock.advance(timedelta(seconds=6))

                with self.assertRaises(JobLeaseConflictError):
                    runtime.heartbeat(claimed.lease)
                recovery = runtime.recover_expired()
                self.assertEqual(recovery.interrupted_count, 1)
                self.assertEqual(recovery.requeued_count, 0)
                self.assertEqual(
                    runtime.get(job.job_id).status,
                    JobStatus.INTERRUPTED,
                )
                self.assertIsNone(runtime.claim_next("worker-b"))

    def test_queued_and_running_jobs_have_distinct_cancel_paths(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            path = Path(directory) / "cancel.verdflow"
            with ProjectStore.create(path, "Cancel") as store:
                runtime = JobRuntime(store, clock=ManualClock(self.initial_time))
                queued = runtime.submit(
                    command_ref(store, b"queued"),
                    idempotency_key="cancel-queued",
                )
                cancelled = runtime.request_cancel(queued.job_id)
                self.assertEqual(cancelled.status, JobStatus.CANCELLED)

                running = runtime.submit(
                    command_ref(store, b"running"),
                    idempotency_key="cancel-running",
                )
                claimed = runtime.claim_next("worker-a")
                assert claimed is not None
                self.assertEqual(claimed.job.job_id, running.job_id)
                cancelling = runtime.request_cancel(running.job_id)
                self.assertEqual(cancelling.status, JobStatus.CANCELLING)
                acknowledged = runtime.acknowledge_cancel(claimed.lease)
                self.assertEqual(acknowledged.status, JobStatus.CANCELLED)

    def test_structured_failure_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            path = Path(directory) / "failure.verdflow"
            with ProjectStore.create(path, "Failure") as store:
                runtime = JobRuntime(store, clock=ManualClock(self.initial_time))
                job = runtime.submit(
                    command_ref(store),
                    idempotency_key="failure-1",
                )
                claimed = runtime.claim_next("worker-a")
                assert claimed is not None
                failed = runtime.fail(
                    claimed.lease,
                    JobErrorInfo(
                        code="TEST_FAILURE",
                        message="Injected worker failure",
                        retryable=False,
                    ),
                )
                self.assertEqual(failed.status, JobStatus.FAILED)
                self.assertEqual(failed.error.code, "TEST_FAILURE")
                self.assertEqual(runtime.get(job.job_id).error, failed.error)

    def test_runtime_restart_recovers_persisted_expired_job(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            path = Path(directory) / "restart.verdflow"
            clock = ManualClock(self.initial_time)
            with ProjectStore.create(path, "Restart") as store:
                runtime = JobRuntime(
                    store,
                    clock=clock,
                    lease_duration=timedelta(seconds=5),
                )
                job = runtime.submit(
                    command_ref(store),
                    idempotency_key="restart-1",
                    max_attempts=2,
                )
                self.assertIsNotNone(runtime.claim_next("worker-a"))

            clock.advance(timedelta(seconds=6))
            with ProjectStore.open(path) as reopened:
                restarted = JobRuntime(
                    reopened,
                    clock=clock,
                    lease_duration=timedelta(seconds=5),
                )
                result = restarted.recover_expired()
                self.assertEqual(result.requeued_count, 1)
                self.assertEqual(
                    restarted.get(job.job_id).status,
                    JobStatus.QUEUED,
                )


if __name__ == "__main__":
    unittest.main()
