"""Persistent coordinator state for deferred composition output."""

from __future__ import annotations

import sqlite3
import uuid

from verdflow.contracts import (
    CompositionOutputExecutionInfo,
    CompositionOutputExecutionStage,
    JobStatus,
)

from .database import ProjectDatabase
from .errors import (
    CompositionOutputExecutionConflictError,
    CompositionOutputExecutionNotFoundError,
)


_SELECT = """
SELECT execution_id, idempotency_key, request_artifact_id, request_hash,
       composition_id, composition_generation, status, stage, revision,
       export_job_id, export_id, error_code, error_message,
       created_at, updated_at
FROM composition_output_executions
"""


class CompositionOutputExecutionRepository:
    """Persist a bounded elevation-then-export coordinator."""

    def __init__(self, database: ProjectDatabase) -> None:
        self.database = database

    def create(
        self,
        *,
        idempotency_key: str,
        request_artifact_id: str,
        request_hash: str,
        composition_id: str,
        composition_generation: int,
        created_at: str,
    ) -> CompositionOutputExecutionInfo:
        if composition_generation < 1:
            raise ValueError("composition_generation must be positive")
        with self.database.transaction() as connection:
            existing = self._by_idempotency(connection, idempotency_key)
            if existing is not None:
                if (
                    existing.request_artifact_id != request_artifact_id
                    or existing.request_hash != request_hash
                ):
                    raise CompositionOutputExecutionConflictError(
                        "Composition output idempotency key was reused"
                    )
                return existing
            artifact = connection.execute(
                "SELECT 1 FROM artifacts WHERE artifact_id = ?",
                (request_artifact_id,),
            ).fetchone()
            if artifact is None:
                raise CompositionOutputExecutionConflictError(
                    "Composition output request Artifact does not exist"
                )
            info = CompositionOutputExecutionInfo(
                execution_id=f"coex_{uuid.uuid4().hex}",
                idempotency_key=idempotency_key,
                request_artifact_id=request_artifact_id,
                request_hash=request_hash,
                composition_id=composition_id,
                composition_generation=composition_generation,
                status=JobStatus.QUEUED,
                stage=CompositionOutputExecutionStage.ELEVATION,
                revision=1,
                export_job_id=None,
                export_id=None,
                error_code=None,
                error_message=None,
                created_at=created_at,
                updated_at=created_at,
            )
            connection.execute(
                """
                INSERT INTO composition_output_executions(
                    execution_id, idempotency_key, request_artifact_id,
                    request_hash, composition_id, composition_generation,
                    status, stage, revision, export_job_id, export_id,
                    error_code, error_message, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _values(info),
            )
            return info

    def get(self, execution_id: str) -> CompositionOutputExecutionInfo:
        row = self.database.connection.execute(
            _SELECT + " WHERE execution_id = ?",
            (execution_id,),
        ).fetchone()
        if row is None:
            raise CompositionOutputExecutionNotFoundError(execution_id)
        return _from_row(row)

    def list_active(
        self,
        *,
        limit: int = 20,
    ) -> tuple[CompositionOutputExecutionInfo, ...]:
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        rows = self.database.connection.execute(
            _SELECT
            + " WHERE status IN ('queued', 'running')"
            + " ORDER BY updated_at, execution_id LIMIT ?",
            (limit,),
        ).fetchall()
        return tuple(_from_row(row) for row in rows)

    def update(
        self,
        info: CompositionOutputExecutionInfo,
        *,
        expected_revision: int,
        updated_at: str,
    ) -> CompositionOutputExecutionInfo:
        updated = CompositionOutputExecutionInfo(
            execution_id=info.execution_id,
            idempotency_key=info.idempotency_key,
            request_artifact_id=info.request_artifact_id,
            request_hash=info.request_hash,
            composition_id=info.composition_id,
            composition_generation=info.composition_generation,
            status=info.status,
            stage=info.stage,
            revision=expected_revision + 1,
            export_job_id=info.export_job_id,
            export_id=info.export_id,
            error_code=info.error_code,
            error_message=info.error_message,
            created_at=info.created_at,
            updated_at=updated_at,
        )
        with self.database.transaction() as connection:
            current = connection.execute(
                _SELECT + " WHERE execution_id = ?",
                (info.execution_id,),
            ).fetchone()
            if current is None:
                raise CompositionOutputExecutionNotFoundError(info.execution_id)
            current_info = _from_row(current)
            if (
                current_info.idempotency_key,
                current_info.request_artifact_id,
                current_info.request_hash,
                current_info.composition_id,
                current_info.composition_generation,
                current_info.created_at,
            ) != (
                info.idempotency_key,
                info.request_artifact_id,
                info.request_hash,
                info.composition_id,
                info.composition_generation,
                info.created_at,
            ):
                raise CompositionOutputExecutionConflictError(
                    "Composition output immutable facts changed"
                )
            cursor = connection.execute(
                """
                UPDATE composition_output_executions
                SET status = ?, stage = ?, revision = ?, export_job_id = ?,
                    export_id = ?, error_code = ?, error_message = ?,
                    updated_at = ?
                WHERE execution_id = ? AND revision = ?
                """,
                (
                    updated.status.value,
                    updated.stage.value,
                    updated.revision,
                    updated.export_job_id,
                    updated.export_id,
                    updated.error_code,
                    updated.error_message,
                    updated.updated_at,
                    updated.execution_id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise CompositionOutputExecutionConflictError(
                    "Composition output execution revision is stale"
                )
        return updated

    @staticmethod
    def _by_idempotency(
        connection: sqlite3.Connection,
        idempotency_key: str,
    ) -> CompositionOutputExecutionInfo | None:
        row = connection.execute(
            _SELECT + " WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        return _from_row(row) if row is not None else None


def _values(info: CompositionOutputExecutionInfo) -> tuple[object, ...]:
    return (
        info.execution_id,
        info.idempotency_key,
        info.request_artifact_id,
        info.request_hash,
        info.composition_id,
        info.composition_generation,
        info.status.value,
        info.stage.value,
        info.revision,
        info.export_job_id,
        info.export_id,
        info.error_code,
        info.error_message,
        info.created_at,
        info.updated_at,
    )


def _from_row(row: object) -> CompositionOutputExecutionInfo:
    return CompositionOutputExecutionInfo(
        execution_id=str(row["execution_id"]),
        idempotency_key=str(row["idempotency_key"]),
        request_artifact_id=str(row["request_artifact_id"]),
        request_hash=str(row["request_hash"]),
        composition_id=str(row["composition_id"]),
        composition_generation=int(row["composition_generation"]),
        status=JobStatus(row["status"]),
        stage=CompositionOutputExecutionStage(row["stage"]),
        revision=int(row["revision"]),
        export_job_id=row["export_job_id"],
        export_id=row["export_id"],
        error_code=row["error_code"],
        error_message=row["error_message"],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )
