"""SQLite row mapping and shared Job persistence guards."""

from __future__ import annotations

import sqlite3

from verdflow.contracts import (
    JobAttemptInfo,
    JobCommandRef,
    JobErrorInfo,
    JobEvent,
    JobEventType,
    JobInfo,
    JobRetentionClass,
    JobStatus,
    JobSubmission,
)

from .errors import JobConflictError, JobNotFoundError


TERMINAL_STATUSES = {
    JobStatus.CANCELLED,
    JobStatus.SUCCEEDED,
    JobStatus.FAILED,
    JobStatus.INTERRUPTED,
}


def get_job_row(
    connection: sqlite3.Connection,
    job_id: str,
) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM jobs WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    if row is None:
        raise JobNotFoundError(job_id)
    return row


def job_info_from_row(row: sqlite3.Row) -> JobInfo:
    error = error_from_row(row)
    return JobInfo(
        job_id=str(row["job_id"]),
        idempotency_key=str(row["idempotency_key"]),
        command=JobCommandRef(
            command_type=str(row["command_type"]),
            command_version=str(row["command_version"]),
            artifact_id=str(row["command_artifact_id"]),
        ),
        status=JobStatus(row["status"]),
        progress=float(row["progress"]),
        stage=str(row["stage"]),
        max_attempts=int(row["max_attempts"]),
        attempt_count=int(row["attempt_count"]),
        worker_id=row["worker_id"],
        heartbeat_at=row["heartbeat_at"],
        lease_expires_at=row["lease_expires_at"],
        created_at=str(row["created_at"]),
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        retention_class=JobRetentionClass(row["retention_class"]),
        retention_seconds=(
            int(row["retention_seconds"])
            if row["retention_seconds"] is not None
            else None
        ),
        purge_after=row["purge_after"],
        result_artifact_id=row["result_artifact_id"],
        error=error,
    )


def job_event_from_row(row: sqlite3.Row) -> JobEvent:
    return JobEvent(
        event_id=int(row["event_id"]),
        job_id=str(row["job_id"]),
        sequence=int(row["sequence"]),
        event_type=JobEventType(row["event_type"]),
        status=JobStatus(row["status"]),
        occurred_at=str(row["occurred_at"]),
        attempt_count=int(row["attempt_count"]),
        progress=float(row["progress"]),
        stage=str(row["stage"]),
        message=row["message"],
    )


def job_attempt_from_row(row: sqlite3.Row) -> JobAttemptInfo:
    return JobAttemptInfo(
        job_id=str(row["job_id"]),
        attempt_number=int(row["attempt_number"]),
        worker_id=str(row["worker_id"]),
        status=JobStatus(row["status"]),
        started_at=str(row["started_at"]),
        heartbeat_at=str(row["heartbeat_at"]),
        lease_expires_at=row["lease_expires_at"],
        finished_at=row["finished_at"],
        error=error_from_row(row),
    )


def error_from_row(row: sqlite3.Row) -> JobErrorInfo | None:
    if row["error_code"] is None:
        return None
    return JobErrorInfo(
        code=str(row["error_code"]),
        message=str(row["error_message"]),
        retryable=bool(row["error_retryable"]),
        diagnostic_artifact_id=row["diagnostic_artifact_id"],
    )


def same_submission(
    row: sqlite3.Row,
    submission: JobSubmission,
) -> bool:
    return (
        row["command_type"] == submission.command.command_type
        and row["command_version"] == submission.command.command_version
        and row["command_artifact_id"] == submission.command.artifact_id
        and int(row["max_attempts"]) == submission.max_attempts
        and row["retention_class"] == submission.retention_class.value
        and (
            int(row["retention_seconds"])
            if row["retention_seconds"] is not None
            else None
        )
        == submission.retention_seconds
    )


def require_attempt_updated(
    cursor: sqlite3.Cursor,
    job_id: str,
    attempt_number: int,
) -> None:
    if cursor.rowcount != 1:
        raise JobConflictError(
            f"Job attempt history is missing: {job_id}#{attempt_number}"
        )
