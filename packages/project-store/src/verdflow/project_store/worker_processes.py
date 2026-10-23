"""Persistence for external processes owned by a Job attempt."""

from __future__ import annotations

from verdflow.contracts import WorkerProcessInfo, WorkerProcessStatus

from .database import ProjectDatabase
from .errors import WorkerProcessConflictError, WorkerProcessNotFoundError


class WorkerProcessRepository:
    def __init__(self, database: ProjectDatabase) -> None:
        self.database = database

    def register(self, process: WorkerProcessInfo) -> WorkerProcessInfo:
        with self.database.transaction() as connection:
            attempt = connection.execute(
                """
                SELECT 1 FROM job_attempts
                WHERE job_id = ? AND attempt_number = ?
                """,
                (process.job_id, process.attempt_number),
            ).fetchone()
            if attempt is None:
                raise WorkerProcessConflictError(
                    "Worker process requires an existing Job attempt"
                )
            existing = connection.execute(
                """
                SELECT * FROM worker_processes
                WHERE job_id = ? AND attempt_number = ?
                """,
                (process.job_id, process.attempt_number),
            ).fetchone()
            if existing is not None:
                current = _worker_process_from_row(existing)
                if current == process:
                    return current
                raise WorkerProcessConflictError(
                    "Job attempt already owns another worker process"
                )
            connection.execute(
                """
                INSERT INTO worker_processes(
                    job_id, attempt_number, adapter_id, host, pid,
                    started_at_token, status, started_at, heartbeat_at,
                    finished_at, exit_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    process.job_id,
                    process.attempt_number,
                    process.adapter_id,
                    process.host,
                    process.pid,
                    process.started_at_token,
                    process.status.value,
                    process.started_at,
                    process.heartbeat_at,
                    process.finished_at,
                    process.exit_code,
                ),
            )
        return process

    def heartbeat(
        self,
        job_id: str,
        attempt_number: int,
        heartbeat_at: str,
    ) -> WorkerProcessInfo:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE worker_processes
                SET status = ?, heartbeat_at = ?
                WHERE job_id = ? AND attempt_number = ?
                  AND status IN (?, ?)
                """,
                (
                    WorkerProcessStatus.RUNNING.value,
                    heartbeat_at,
                    job_id,
                    attempt_number,
                    WorkerProcessStatus.STARTING.value,
                    WorkerProcessStatus.RUNNING.value,
                ),
            )
            if cursor.rowcount != 1:
                raise WorkerProcessConflictError(
                    "Worker process is missing or no longer active"
                )
        return self.get(job_id, attempt_number)

    def finish(
        self,
        job_id: str,
        attempt_number: int,
        *,
        status: WorkerProcessStatus,
        finished_at: str,
        exit_code: int | None,
    ) -> WorkerProcessInfo:
        if status not in {
            WorkerProcessStatus.EXITED,
            WorkerProcessStatus.TERMINATED,
            WorkerProcessStatus.LOST,
        }:
            raise ValueError("Worker process final status is invalid")
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE worker_processes
                SET status = ?, finished_at = ?, exit_code = ?
                WHERE job_id = ? AND attempt_number = ?
                  AND status IN (?, ?)
                """,
                (
                    status.value,
                    finished_at,
                    exit_code,
                    job_id,
                    attempt_number,
                    WorkerProcessStatus.STARTING.value,
                    WorkerProcessStatus.RUNNING.value,
                ),
            )
            if cursor.rowcount != 1:
                raise WorkerProcessConflictError(
                    "Worker process is missing or already finalized"
                )
        return self.get(job_id, attempt_number)

    def get(
        self,
        job_id: str,
        attempt_number: int,
    ) -> WorkerProcessInfo:
        row = self.database.connection.execute(
            """
            SELECT * FROM worker_processes
            WHERE job_id = ? AND attempt_number = ?
            """,
            (job_id, attempt_number),
        ).fetchone()
        if row is None:
            raise WorkerProcessNotFoundError(
                f"{job_id}:{attempt_number}"
            )
        return _worker_process_from_row(row)

    def list_active(self) -> tuple[WorkerProcessInfo, ...]:
        rows = self.database.connection.execute(
            """
            SELECT * FROM worker_processes
            WHERE status IN (?, ?)
            ORDER BY started_at, job_id, attempt_number
            """,
            (
                WorkerProcessStatus.STARTING.value,
                WorkerProcessStatus.RUNNING.value,
            ),
        ).fetchall()
        return tuple(_worker_process_from_row(row) for row in rows)


def _worker_process_from_row(row: object) -> WorkerProcessInfo:
    return WorkerProcessInfo(
        job_id=row["job_id"],
        attempt_number=int(row["attempt_number"]),
        adapter_id=row["adapter_id"],
        host=row["host"],
        pid=int(row["pid"]),
        started_at_token=row["started_at_token"],
        status=WorkerProcessStatus(row["status"]),
        started_at=row["started_at"],
        heartbeat_at=row["heartbeat_at"],
        finished_at=row["finished_at"],
        exit_code=row["exit_code"],
    )
