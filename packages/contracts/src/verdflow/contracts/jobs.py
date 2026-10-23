"""Persistent job contracts shared by Core, workers and clients."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_USER = "awaiting_user"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class JobRetentionClass(StrEnum):
    TEMPORARY = "temporary"
    CANDIDATE = "candidate"
    RUN = "run"
    AUDIT = "audit"


class JobEventType(StrEnum):
    SUBMITTED = "submitted"
    STARTED = "started"
    PROGRESS = "progress"
    CANCELLATION_REQUESTED = "cancellation_requested"
    CANCELLED = "cancelled"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    RETRY_SCHEDULED = "retry_scheduled"


@dataclass(frozen=True, slots=True)
class JobCommandRef:
    command_type: str
    command_version: str
    artifact_id: str


@dataclass(frozen=True, slots=True)
class JobSubmission:
    command: JobCommandRef
    idempotency_key: str
    max_attempts: int = 1
    retention_class: JobRetentionClass = JobRetentionClass.RUN
    retention_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class JobErrorInfo:
    code: str
    message: str
    retryable: bool
    diagnostic_artifact_id: str | None = None


@dataclass(frozen=True, slots=True)
class JobInfo:
    job_id: str
    idempotency_key: str
    command: JobCommandRef
    status: JobStatus
    progress: float
    stage: str
    max_attempts: int
    attempt_count: int
    worker_id: str | None
    heartbeat_at: str | None
    lease_expires_at: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    retention_class: JobRetentionClass
    retention_seconds: int | None
    purge_after: str | None
    result_artifact_id: str | None
    error: JobErrorInfo | None


@dataclass(frozen=True, slots=True)
class JobLease:
    job_id: str
    lease_token: str
    worker_id: str
    attempt_count: int
    expires_at: str


@dataclass(frozen=True, slots=True)
class JobAttemptInfo:
    job_id: str
    attempt_number: int
    worker_id: str
    status: JobStatus
    started_at: str
    heartbeat_at: str
    lease_expires_at: str | None
    finished_at: str | None
    error: JobErrorInfo | None


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    job: JobInfo
    lease: JobLease


@dataclass(frozen=True, slots=True)
class JobEvent:
    event_id: int
    job_id: str
    sequence: int
    event_type: JobEventType
    status: JobStatus
    occurred_at: str
    attempt_count: int
    progress: float
    stage: str
    message: str | None = None


@dataclass(frozen=True, slots=True)
class JobRecoveryResult:
    interrupted_count: int
    requeued_count: int
    cancelled_count: int


@dataclass(frozen=True, slots=True)
class JobRetentionSweepResult:
    examined_job_count: int
    removed_job_count: int
    protected_job_count: int
    removed_assignment_count: int
    removed_snapshot_count: int
    removed_artifact_count: int
    removed_object_count: int
    removed_byte_size: int


@dataclass(frozen=True, slots=True)
class JobListCursor:
    created_at: str
    job_id: str


@dataclass(frozen=True, slots=True)
class JobListPage:
    items: tuple[JobInfo, ...]
    next_cursor: JobListCursor | None
