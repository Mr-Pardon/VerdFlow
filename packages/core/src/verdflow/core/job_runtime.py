"""Persistent Job state machine independent of transports and workers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from verdflow.contracts import (
    ClaimedJob,
    JobAttemptInfo,
    JobCommandRef,
    JobErrorInfo,
    JobEvent,
    JobInfo,
    JobLease,
    JobRecoveryResult,
    JobRetentionClass,
    JobSubmission,
)
from verdflow.project_store import ProjectStore

from .clock import Clock, SystemClock
from .retention import JobRetentionPolicy


class JobRuntime:
    def __init__(
        self,
        store: ProjectStore,
        *,
        clock: Clock | None = None,
        lease_duration: timedelta = timedelta(seconds=30),
        retention_policy: JobRetentionPolicy | None = None,
    ) -> None:
        if lease_duration.total_seconds() <= 0:
            raise ValueError("lease_duration must be positive")
        self.store = store
        self.clock = clock or SystemClock()
        self.lease_duration = lease_duration
        self.retention_policy = retention_policy or JobRetentionPolicy()

    def submit(
        self,
        command: JobCommandRef,
        *,
        idempotency_key: str,
        max_attempts: int = 1,
        retention_class: JobRetentionClass = JobRetentionClass.RUN,
    ) -> JobInfo:
        self._require_text(command.command_type, "command_type")
        self._require_text(command.command_version, "command_version")
        self._require_text(command.artifact_id, "command artifact_id")
        self._require_text(idempotency_key, "idempotency_key")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if not isinstance(retention_class, JobRetentionClass):
            raise ValueError("retention_class is invalid")
        return self.store.create_job(
            JobSubmission(
                command=command,
                idempotency_key=idempotency_key,
                max_attempts=max_attempts,
                retention_class=retention_class,
                retention_seconds=self.retention_policy.seconds_for(
                    retention_class
                ),
            ),
            self._now_text(),
        )

    def get(self, job_id: str) -> JobInfo:
        return self.store.get_job(job_id)

    def promote(
        self,
        job_id: str,
        *,
        retention_class: JobRetentionClass = JobRetentionClass.RUN,
    ) -> JobInfo:
        self._require_text(job_id, "job_id")
        return self.store.promote_job_retention(job_id, retention_class)

    def claim_next(
        self,
        worker_id: str,
        *,
        command_types: tuple[str, ...] | None = None,
    ) -> ClaimedJob | None:
        self._require_text(worker_id, "worker_id")
        if command_types is not None:
            if not command_types:
                raise ValueError("command_types cannot be empty")
            for command_type in command_types:
                self._require_text(command_type, "command_type")
        now = self._now()
        return self.store.claim_next_job(
            worker_id=worker_id,
            now=now.isoformat(),
            lease_expires_at=(now + self.lease_duration).isoformat(),
            command_types=command_types,
        )

    def heartbeat(
        self,
        lease: JobLease,
        *,
        progress: float | None = None,
        stage: str | None = None,
        lease_duration: timedelta | None = None,
    ) -> JobInfo:
        if progress is not None and not 0.0 <= progress <= 1.0:
            raise ValueError("progress must be between 0.0 and 1.0")
        if stage is not None:
            self._require_text(stage, "stage")
        duration = lease_duration or self.lease_duration
        if duration.total_seconds() <= 0:
            raise ValueError("lease_duration must be positive")
        now = self._now()
        return self.store.heartbeat_job(
            lease,
            now=now.isoformat(),
            lease_expires_at=(now + duration).isoformat(),
            progress=progress,
            stage=stage,
        )

    def succeed(
        self,
        lease: JobLease,
        *,
        result_artifact_id: str | None = None,
    ) -> JobInfo:
        if result_artifact_id is not None:
            self._require_text(result_artifact_id, "result_artifact_id")
        return self.store.succeed_job(
            lease,
            now=self._now_text(),
            result_artifact_id=result_artifact_id,
        )

    def fail(
        self,
        lease: JobLease,
        error: JobErrorInfo,
    ) -> JobInfo:
        self._require_text(error.code, "error code")
        self._require_text(error.message, "error message")
        return self.store.fail_job(
            lease,
            now=self._now_text(),
            error=error,
        )

    def request_cancel(self, job_id: str) -> JobInfo:
        self._require_text(job_id, "job_id")
        return self.store.request_job_cancel(job_id, self._now_text())

    def acknowledge_cancel(self, lease: JobLease) -> JobInfo:
        return self.store.acknowledge_job_cancel(
            lease,
            self._now_text(),
        )

    def recover_expired(self) -> JobRecoveryResult:
        return self.store.recover_expired_jobs(self._now_text())

    def events(
        self,
        job_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> tuple[JobEvent, ...]:
        if after_sequence < 0:
            raise ValueError("after_sequence cannot be negative")
        return self.store.list_job_events(
            job_id,
            after_sequence=after_sequence,
            limit=limit,
        )

    def attempts(self, job_id: str) -> tuple[JobAttemptInfo, ...]:
        self._require_text(job_id, "job_id")
        return self.store.list_job_attempts(job_id)

    def _now_text(self) -> str:
        return self._now().isoformat()

    def _now(self) -> datetime:
        value = self.clock.now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Clock must return a timezone-aware datetime")
        return value.astimezone(UTC)

    @staticmethod
    def _require_text(value: str, field: str) -> None:
        if not value.strip():
            raise ValueError(f"{field} is required")
