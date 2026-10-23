"""Monotonic JobEvent append and replay operations."""

from __future__ import annotations

import sqlite3

from verdflow.contracts import JobEvent, JobEventType, JobStatus

from .database import ProjectDatabase
from .job_records import job_event_from_row


class JobEventStore:
    def __init__(self, database: ProjectDatabase) -> None:
        self.database = database

    def append(
        self,
        connection: sqlite3.Connection,
        *,
        job_id: str,
        event_type: JobEventType,
        status: JobStatus,
        occurred_at: str,
        attempt_count: int,
        progress: float,
        stage: str,
        message: str | None,
    ) -> None:
        row = connection.execute(
            """
            SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence
            FROM job_events
            WHERE job_id = ?
            """,
            (job_id,),
        ).fetchone()
        connection.execute(
            """
            INSERT INTO job_events(
                job_id, sequence, event_type, status, occurred_at,
                attempt_count, progress, stage, message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                int(row["next_sequence"]),
                event_type.value,
                status.value,
                occurred_at,
                attempt_count,
                progress,
                stage,
                message,
            ),
        )

    def list(
        self,
        job_id: str,
        *,
        after_sequence: int,
        limit: int,
    ) -> tuple[JobEvent, ...]:
        rows = self.database.connection.execute(
            """
            SELECT *
            FROM job_events
            WHERE job_id = ? AND sequence > ?
            ORDER BY sequence
            LIMIT ?
            """,
            (job_id, after_sequence, limit),
        ).fetchall()
        return tuple(job_event_from_row(row) for row in rows)
