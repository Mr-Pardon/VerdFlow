"""Atomic convergence of expired running and cancelling Job attempts."""

from __future__ import annotations

import sqlite3

from verdflow.contracts import (
    JobEventType,
    JobRecoveryResult,
    JobStatus,
)

from .database import ProjectDatabase
from .job_events import JobEventStore
from .job_records import (
    get_job_row,
    job_info_from_row,
    require_attempt_updated,
)
from .job_retention import purge_after_for


class ExpiredJobRecovery:
    def __init__(
        self,
        database: ProjectDatabase,
        events: JobEventStore,
    ) -> None:
        self.database = database
        self.events = events

    def recover(self, now: str) -> JobRecoveryResult:
        interrupted = 0
        requeued = 0
        cancelled = 0
        with self.database.transaction() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM jobs
                WHERE status IN (?, ?)
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at <= ?
                ORDER BY lease_expires_at, job_id
                """,
                (
                    JobStatus.RUNNING.value,
                    JobStatus.CANCELLING.value,
                    now,
                ),
            ).fetchall()
            for row in rows:
                job_id = str(row["job_id"])
                attempt_number = int(row["attempt_count"])
                status = JobStatus(row["status"])
                if status == JobStatus.CANCELLING:
                    connection.execute(
                        """
                        UPDATE jobs
                        SET status = ?, stage = 'cancelled',
                            lease_token = NULL, lease_expires_at = NULL,
                            finished_at = ?, purge_after = ?
                        WHERE job_id = ?
                        """,
                        (
                            JobStatus.CANCELLED.value,
                            now,
                            purge_after_for(row, now),
                            job_id,
                        ),
                    )
                    cursor = connection.execute(
                        """
                        UPDATE job_attempts
                        SET status = ?, lease_expires_at = NULL,
                            finished_at = ?
                        WHERE job_id = ? AND attempt_number = ?
                        """,
                        (
                            JobStatus.CANCELLED.value,
                            now,
                            job_id,
                            attempt_number,
                        ),
                    )
                    require_attempt_updated(cursor, job_id, attempt_number)
                    self._append_from_job(
                        connection,
                        job_id,
                        JobEventType.CANCELLED,
                        now,
                        "Worker lease expired while cancelling",
                    )
                    cancelled += 1
                    continue

                connection.execute(
                    """
                    UPDATE jobs
                    SET status = ?, stage = 'interrupted',
                        lease_token = NULL, lease_expires_at = NULL,
                        finished_at = ?, purge_after = ?
                    WHERE job_id = ?
                    """,
                    (
                        JobStatus.INTERRUPTED.value,
                        now,
                        purge_after_for(row, now),
                        job_id,
                    ),
                )
                cursor = connection.execute(
                    """
                    UPDATE job_attempts
                    SET status = ?, lease_expires_at = NULL,
                        finished_at = ?
                    WHERE job_id = ? AND attempt_number = ?
                    """,
                    (
                        JobStatus.INTERRUPTED.value,
                        now,
                        job_id,
                        attempt_number,
                    ),
                )
                require_attempt_updated(cursor, job_id, attempt_number)
                self._append_from_job(
                    connection,
                    job_id,
                    JobEventType.INTERRUPTED,
                    now,
                    "Worker heartbeat lease expired",
                )
                interrupted += 1
                if attempt_number < int(row["max_attempts"]):
                    connection.execute(
                        """
                        UPDATE jobs
                        SET status = ?, progress = 0.0, stage = 'queued',
                            worker_id = NULL, heartbeat_at = NULL,
                            finished_at = NULL, purge_after = NULL
                        WHERE job_id = ?
                        """,
                        (JobStatus.QUEUED.value, job_id),
                    )
                    self._append_from_job(
                        connection,
                        job_id,
                        JobEventType.RETRY_SCHEDULED,
                        now,
                        "Interrupted attempt returned to the queue",
                    )
                    requeued += 1
        return JobRecoveryResult(
            interrupted_count=interrupted,
            requeued_count=requeued,
            cancelled_count=cancelled,
        )

    def _append_from_job(
        self,
        connection: sqlite3.Connection,
        job_id: str,
        event_type: JobEventType,
        occurred_at: str,
        message: str,
    ) -> None:
        row = get_job_row(connection, job_id)
        job = job_info_from_row(row)
        self.events.append(
            connection,
            job_id=job.job_id,
            event_type=event_type,
            status=job.status,
            occurred_at=occurred_at,
            attempt_count=job.attempt_count,
            progress=job.progress,
            stage=job.stage,
            message=message,
        )
