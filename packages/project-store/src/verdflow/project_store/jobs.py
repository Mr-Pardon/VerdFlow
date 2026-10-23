"""Transactional persistence for Job snapshots, leases and event replay."""

from __future__ import annotations

import sqlite3
import uuid

from verdflow.contracts import (
    ClaimedJob,
    JobAttemptInfo,
    JobErrorInfo,
    JobEvent,
    JobEventType,
    JobInfo,
    JobLease,
    JobListCursor,
    JobListPage,
    JobRetentionClass,
    JobRecoveryResult,
    JobStatus,
    JobSubmission,
)

from .database import ProjectDatabase
from .errors import (
    ArtifactNotFoundError,
    JobConflictError,
    JobIdempotencyConflictError,
    JobLeaseConflictError,
    JobNotFoundError,
)
from .job_events import JobEventStore
from .job_records import (
    TERMINAL_STATUSES,
    get_job_row,
    job_attempt_from_row,
    job_info_from_row,
    require_attempt_updated,
    same_submission,
)
from .job_retention import purge_after_for, validate_retention
from .job_recovery import ExpiredJobRecovery


class JobRepository:
    def __init__(self, database: ProjectDatabase) -> None:
        self.database = database
        self._events = JobEventStore(database)
        self._recovery = ExpiredJobRecovery(database, self._events)

    def create(self, submission: JobSubmission, now: str) -> JobInfo:
        validate_retention(
            submission.retention_class,
            submission.retention_seconds,
        )
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM jobs WHERE idempotency_key = ?",
                (submission.idempotency_key,),
            ).fetchone()
            if existing is not None:
                if not same_submission(existing, submission):
                    raise JobIdempotencyConflictError(
                        "Idempotency key belongs to a different job command"
                    )
                return job_info_from_row(existing)

            self._require_artifact(
                connection,
                submission.command.artifact_id,
            )
            job_id = f"job_{uuid.uuid4().hex}"
            connection.execute(
                """
                INSERT INTO jobs(
                    job_id, idempotency_key, command_type, command_version,
                    command_artifact_id, status, progress, stage,
                    max_attempts, attempt_count, created_at,
                    retention_class, retention_seconds
                ) VALUES (?, ?, ?, ?, ?, ?, 0.0, 'queued', ?, 0, ?, ?, ?)
                """,
                (
                    job_id,
                    submission.idempotency_key,
                    submission.command.command_type,
                    submission.command.command_version,
                    submission.command.artifact_id,
                    JobStatus.QUEUED.value,
                    submission.max_attempts,
                    now,
                    submission.retention_class.value,
                    submission.retention_seconds,
                ),
            )
            self._events.append(
                connection,
                job_id=job_id,
                event_type=JobEventType.SUBMITTED,
                status=JobStatus.QUEUED,
                occurred_at=now,
                attempt_count=0,
                progress=0.0,
                stage="queued",
                message=None,
            )
            return self._get_in_transaction(connection, job_id)

    def get(self, job_id: str) -> JobInfo:
        row = self.database.connection.execute(
            "SELECT * FROM jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            raise JobNotFoundError(job_id)
        return job_info_from_row(row)

    def list_page(
        self,
        *,
        statuses: tuple[JobStatus, ...] = (),
        before: JobListCursor | None = None,
        limit: int = 50,
    ) -> JobListPage:
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        normalized_statuses = tuple(JobStatus(item) for item in statuses)
        if len(normalized_statuses) != len(set(normalized_statuses)):
            raise ValueError("statuses cannot contain duplicates")
        predicates: list[str] = []
        parameters: list[object] = []
        if normalized_statuses:
            placeholders = ", ".join("?" for _ in normalized_statuses)
            predicates.append(f"status IN ({placeholders})")
            parameters.extend(item.value for item in normalized_statuses)
        if before is not None:
            if not isinstance(before, JobListCursor):
                raise ValueError("before cursor is invalid")
            if not before.created_at or not before.job_id:
                raise ValueError("before cursor fields are required")
            predicates.append("(created_at < ? OR (created_at = ? AND job_id < ?))")
            parameters.extend(
                (before.created_at, before.created_at, before.job_id)
            )
        where = " WHERE " + " AND ".join(predicates) if predicates else ""
        rows = self.database.connection.execute(
            "SELECT * FROM jobs"
            + where
            + " ORDER BY created_at DESC, job_id DESC LIMIT ?",
            (*parameters, limit + 1),
        ).fetchall()
        visible = rows[:limit]
        items = tuple(job_info_from_row(row) for row in visible)
        next_cursor = None
        if len(rows) > limit and visible:
            last = visible[-1]
            next_cursor = JobListCursor(
                created_at=str(last["created_at"]),
                job_id=str(last["job_id"]),
            )
        return JobListPage(items=items, next_cursor=next_cursor)

    def promote_retention(
        self,
        job_id: str,
        retention_class: JobRetentionClass,
    ) -> JobInfo:
        if retention_class not in {
            JobRetentionClass.RUN,
            JobRetentionClass.AUDIT,
        }:
            raise ValueError("Jobs can only be promoted to run or audit")
        with self.database.transaction() as connection:
            current = get_job_row(connection, job_id)
            status = JobStatus(current["status"])
            if status not in TERMINAL_STATUSES:
                raise JobConflictError(
                    "Only terminal Jobs can be promoted"
                )
            current_class = JobRetentionClass(current["retention_class"])
            if current_class in {
                JobRetentionClass.RUN,
                JobRetentionClass.AUDIT,
            }:
                if current_class is not retention_class:
                    raise JobConflictError(
                        "Permanent Job retention cannot be changed"
                    )
                return job_info_from_row(current)
            connection.execute(
                """
                UPDATE jobs
                SET retention_class = ?, retention_seconds = NULL,
                    purge_after = NULL
                WHERE job_id = ?
                """,
                (retention_class.value, job_id),
            )
            return self._get_in_transaction(connection, job_id)

    def claim_next(
        self,
        *,
        worker_id: str,
        now: str,
        lease_expires_at: str,
        command_types: tuple[str, ...] | None = None,
    ) -> ClaimedJob | None:
        with self.database.transaction() as connection:
            parameters: list[str] = [JobStatus.QUEUED.value]
            command_filter = ""
            if command_types:
                placeholders = ", ".join("?" for _ in command_types)
                command_filter = f" AND command_type IN ({placeholders})"
                parameters.extend(command_types)
            candidate = connection.execute(
                f"""
                SELECT job_id
                FROM jobs
                WHERE status = ?
                {command_filter}
                ORDER BY created_at, job_id
                LIMIT 1
                """,
                parameters,
            ).fetchone()
            if candidate is None:
                return None

            job_id = str(candidate["job_id"])
            lease_token = f"lease_{uuid.uuid4().hex}"
            cursor = connection.execute(
                """
                UPDATE jobs
                SET status = ?, stage = 'running',
                    attempt_count = attempt_count + 1,
                    worker_id = ?, lease_token = ?,
                    heartbeat_at = ?, lease_expires_at = ?,
                    started_at = COALESCE(started_at, ?),
                    finished_at = NULL,
                    error_code = NULL, error_message = NULL,
                    error_retryable = NULL, diagnostic_artifact_id = NULL
                WHERE job_id = ? AND status = ?
                """,
                (
                    JobStatus.RUNNING.value,
                    worker_id,
                    lease_token,
                    now,
                    lease_expires_at,
                    now,
                    job_id,
                    JobStatus.QUEUED.value,
                ),
            )
            if cursor.rowcount != 1:
                raise JobConflictError(
                    f"Queued job changed before claim: {job_id}"
                )
            job = self._get_in_transaction(connection, job_id)
            connection.execute(
                """
                INSERT INTO job_attempts(
                    job_id, attempt_number, worker_id, lease_token,
                    status, started_at, heartbeat_at, lease_expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    job.attempt_count,
                    worker_id,
                    lease_token,
                    JobStatus.RUNNING.value,
                    now,
                    now,
                    lease_expires_at,
                ),
            )
            self._events.append(
                connection,
                job_id=job_id,
                event_type=JobEventType.STARTED,
                status=JobStatus.RUNNING,
                occurred_at=now,
                attempt_count=job.attempt_count,
                progress=job.progress,
                stage=job.stage,
                message=None,
            )
            return ClaimedJob(
                job=job,
                lease=JobLease(
                    job_id=job_id,
                    lease_token=lease_token,
                    worker_id=worker_id,
                    attempt_count=job.attempt_count,
                    expires_at=lease_expires_at,
                ),
            )

    def heartbeat(
        self,
        lease: JobLease,
        *,
        now: str,
        lease_expires_at: str,
        progress: float | None,
        stage: str | None,
    ) -> JobInfo:
        with self.database.transaction() as connection:
            current = self._require_lease(
                connection,
                lease,
                now=now,
                allowed={JobStatus.RUNNING, JobStatus.CANCELLING},
            )
            current_progress = float(current["progress"])
            next_progress = (
                current_progress if progress is None else progress
            )
            if next_progress < current_progress:
                raise JobConflictError("Job progress cannot move backwards")
            next_stage = str(current["stage"]) if stage is None else stage
            changed = (
                next_progress != current_progress
                or next_stage != current["stage"]
            )
            connection.execute(
                """
                UPDATE jobs
                SET progress = ?, stage = ?, heartbeat_at = ?,
                    lease_expires_at = ?
                WHERE job_id = ?
                """,
                (
                    next_progress,
                    next_stage,
                    now,
                    lease_expires_at,
                    lease.job_id,
                ),
            )
            attempt_cursor = connection.execute(
                """
                UPDATE job_attempts
                SET heartbeat_at = ?, lease_expires_at = ?
                WHERE job_id = ? AND attempt_number = ?
                """,
                (
                    now,
                    lease_expires_at,
                    lease.job_id,
                    lease.attempt_count,
                ),
            )
            require_attempt_updated(
                attempt_cursor,
                lease.job_id,
                lease.attempt_count,
            )
            job = self._get_in_transaction(connection, lease.job_id)
            if changed:
                self._events.append(
                    connection,
                    job_id=job.job_id,
                    event_type=JobEventType.PROGRESS,
                    status=job.status,
                    occurred_at=now,
                    attempt_count=job.attempt_count,
                    progress=job.progress,
                    stage=job.stage,
                    message=None,
                )
            return job

    def succeed(
        self,
        lease: JobLease,
        *,
        now: str,
        result_artifact_id: str | None,
    ) -> JobInfo:
        with self.database.transaction() as connection:
            current = self._require_lease(
                connection,
                lease,
                now=now,
                allowed={JobStatus.RUNNING},
            )
            if result_artifact_id is not None:
                self._require_artifact(connection, result_artifact_id)
            connection.execute(
                """
                UPDATE jobs
                SET status = ?, progress = 1.0, stage = 'completed',
                    heartbeat_at = ?, lease_expires_at = NULL,
                    lease_token = NULL, finished_at = ?,
                    result_artifact_id = ?,
                    purge_after = ?,
                    error_code = NULL, error_message = NULL,
                    error_retryable = NULL, diagnostic_artifact_id = NULL
                WHERE job_id = ?
                """,
                (
                    JobStatus.SUCCEEDED.value,
                    now,
                    now,
                    result_artifact_id,
                    purge_after_for(current, now),
                    lease.job_id,
                ),
            )
            attempt_cursor = connection.execute(
                """
                UPDATE job_attempts
                SET status = ?, heartbeat_at = ?,
                    lease_expires_at = NULL, finished_at = ?
                WHERE job_id = ? AND attempt_number = ?
                """,
                (
                    JobStatus.SUCCEEDED.value,
                    now,
                    now,
                    lease.job_id,
                    lease.attempt_count,
                ),
            )
            require_attempt_updated(
                attempt_cursor,
                lease.job_id,
                lease.attempt_count,
            )
            job = self._get_in_transaction(connection, lease.job_id)
            self._events.append(
                connection,
                job_id=job.job_id,
                event_type=JobEventType.SUCCEEDED,
                status=job.status,
                occurred_at=now,
                attempt_count=int(current["attempt_count"]),
                progress=job.progress,
                stage=job.stage,
                message=None,
            )
            return job

    def fail(
        self,
        lease: JobLease,
        *,
        now: str,
        error: JobErrorInfo,
    ) -> JobInfo:
        with self.database.transaction() as connection:
            current = self._require_lease(
                connection,
                lease,
                now=now,
                allowed={JobStatus.RUNNING},
            )
            if error.diagnostic_artifact_id is not None:
                self._require_artifact(
                    connection,
                    error.diagnostic_artifact_id,
                )
            connection.execute(
                """
                UPDATE jobs
                SET status = ?, stage = 'failed',
                    heartbeat_at = ?, lease_expires_at = NULL,
                    lease_token = NULL, finished_at = ?,
                    error_code = ?, error_message = ?,
                    error_retryable = ?, diagnostic_artifact_id = ?,
                    purge_after = ?
                WHERE job_id = ?
                """,
                (
                    JobStatus.FAILED.value,
                    now,
                    now,
                    error.code,
                    error.message,
                    int(error.retryable),
                    error.diagnostic_artifact_id,
                    purge_after_for(current, now),
                    lease.job_id,
                ),
            )
            attempt_cursor = connection.execute(
                """
                UPDATE job_attempts
                SET status = ?, heartbeat_at = ?,
                    lease_expires_at = NULL, finished_at = ?,
                    error_code = ?, error_message = ?,
                    error_retryable = ?, diagnostic_artifact_id = ?
                WHERE job_id = ? AND attempt_number = ?
                """,
                (
                    JobStatus.FAILED.value,
                    now,
                    now,
                    error.code,
                    error.message,
                    int(error.retryable),
                    error.diagnostic_artifact_id,
                    lease.job_id,
                    lease.attempt_count,
                ),
            )
            require_attempt_updated(
                attempt_cursor,
                lease.job_id,
                lease.attempt_count,
            )
            job = self._get_in_transaction(connection, lease.job_id)
            self._events.append(
                connection,
                job_id=job.job_id,
                event_type=JobEventType.FAILED,
                status=job.status,
                occurred_at=now,
                attempt_count=int(current["attempt_count"]),
                progress=job.progress,
                stage=job.stage,
                message=error.message,
            )
            return job

    def request_cancel(self, job_id: str, now: str) -> JobInfo:
        with self.database.transaction() as connection:
            current = get_job_row(connection, job_id)
            status = JobStatus(current["status"])
            if status in TERMINAL_STATUSES or status == JobStatus.CANCELLING:
                return job_info_from_row(current)
            if status == JobStatus.QUEUED:
                next_status = JobStatus.CANCELLED
                event_type = JobEventType.CANCELLED
                stage = "cancelled"
                finished_at = now
                purge_after = purge_after_for(current, now)
            elif status == JobStatus.RUNNING:
                next_status = JobStatus.CANCELLING
                event_type = JobEventType.CANCELLATION_REQUESTED
                stage = "cancelling"
                finished_at = None
                purge_after = None
            else:
                raise JobConflictError(
                    f"Job {job_id} cannot be cancelled from {status.value}"
                )
            connection.execute(
                """
                UPDATE jobs
                SET status = ?, stage = ?, finished_at = ?, purge_after = ?
                WHERE job_id = ?
                """,
                (next_status.value, stage, finished_at, purge_after, job_id),
            )
            job = self._get_in_transaction(connection, job_id)
            self._events.append(
                connection,
                job_id=job.job_id,
                event_type=event_type,
                status=job.status,
                occurred_at=now,
                attempt_count=job.attempt_count,
                progress=job.progress,
                stage=job.stage,
                message=None,
            )
            return job

    def acknowledge_cancel(
        self,
        lease: JobLease,
        now: str,
    ) -> JobInfo:
        with self.database.transaction() as connection:
            current = self._require_lease(
                connection,
                lease,
                now=now,
                allowed={JobStatus.CANCELLING},
            )
            connection.execute(
                """
                UPDATE jobs
                SET status = ?, stage = 'cancelled',
                    heartbeat_at = ?, lease_expires_at = NULL,
                    lease_token = NULL, finished_at = ?, purge_after = ?
                WHERE job_id = ?
                """,
                (
                    JobStatus.CANCELLED.value,
                    now,
                    now,
                    purge_after_for(current, now),
                    lease.job_id,
                ),
            )
            attempt_cursor = connection.execute(
                """
                UPDATE job_attempts
                SET status = ?, heartbeat_at = ?,
                    lease_expires_at = NULL, finished_at = ?
                WHERE job_id = ? AND attempt_number = ?
                """,
                (
                    JobStatus.CANCELLED.value,
                    now,
                    now,
                    lease.job_id,
                    lease.attempt_count,
                ),
            )
            require_attempt_updated(
                attempt_cursor,
                lease.job_id,
                lease.attempt_count,
            )
            job = self._get_in_transaction(connection, lease.job_id)
            self._events.append(
                connection,
                job_id=job.job_id,
                event_type=JobEventType.CANCELLED,
                status=job.status,
                occurred_at=now,
                attempt_count=int(current["attempt_count"]),
                progress=job.progress,
                stage=job.stage,
                message=None,
            )
            return job

    def recover_expired(self, now: str) -> JobRecoveryResult:
        return self._recovery.recover(now)

    def list_events(
        self,
        job_id: str,
        *,
        after_sequence: int,
        limit: int,
    ) -> tuple[JobEvent, ...]:
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        self.get(job_id)
        return self._events.list(
            job_id,
            after_sequence=after_sequence,
            limit=limit,
        )

    def list_attempts(self, job_id: str) -> tuple[JobAttemptInfo, ...]:
        self.get(job_id)
        rows = self.database.connection.execute(
            """
            SELECT *
            FROM job_attempts
            WHERE job_id = ?
            ORDER BY attempt_number
            """,
            (job_id,),
        ).fetchall()
        return tuple(job_attempt_from_row(row) for row in rows)

    def _require_lease(
        self,
        connection: sqlite3.Connection,
        lease: JobLease,
        *,
        now: str,
        allowed: set[JobStatus],
    ) -> sqlite3.Row:
        row = get_job_row(connection, lease.job_id)
        status = JobStatus(row["status"])
        if (
            status not in allowed
            or row["lease_token"] != lease.lease_token
            or row["worker_id"] != lease.worker_id
            or int(row["attempt_count"]) != lease.attempt_count
            or row["lease_expires_at"] is None
            or row["lease_expires_at"] <= now
        ):
            raise JobLeaseConflictError(
                f"Job lease is stale or invalid: {lease.job_id}"
            )
        return row

    @staticmethod
    def _require_artifact(
        connection: sqlite3.Connection,
        artifact_id: str,
    ) -> None:
        row = connection.execute(
            "SELECT 1 FROM artifacts WHERE artifact_id = ?",
            (artifact_id,),
        ).fetchone()
        if row is None:
            raise ArtifactNotFoundError(artifact_id)

    def _get_in_transaction(
        self,
        connection: sqlite3.Connection,
        job_id: str,
    ) -> JobInfo:
        return job_info_from_row(get_job_row(connection, job_id))
