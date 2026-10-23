"""Persistent coordinator read model for composed planting runs."""

from __future__ import annotations

import sqlite3
import uuid

from verdflow.contracts import (
    PlantRunExecutionInfo,
    PlantRunExecutionStage,
    PlantRunExecutionStatus,
)

from .database import ProjectDatabase
from .errors import (
    PlantRunExecutionConflictError,
    PlantRunExecutionNotFoundError,
)


_SELECT = """
SELECT execution_id, idempotency_key, request_artifact_id, request_hash,
       continuation_artifact_id, continuation_hash,
       status, stage, revision, generation_job_id, generated_point_set_id,
       assignment_job_id, asset_assignment_set_id, elevation_job_id,
       elevation_point_set_id, export_job_id, export_id, error_code,
       error_message, created_at, updated_at
FROM plant_run_executions
"""


class PlantRunExecutionRepository:
    """Persist coordinator state without interpreting workflow policy."""

    def __init__(self, database: ProjectDatabase) -> None:
        self.database = database

    def create(
        self,
        *,
        idempotency_key: str,
        request_artifact_id: str,
        request_hash: str,
        created_at: str,
    ) -> PlantRunExecutionInfo:
        idempotency_key = self._required(idempotency_key, "idempotency_key")
        request_artifact_id = self._required(
            request_artifact_id,
            "request_artifact_id",
        )
        request_hash = self._required(request_hash, "request_hash")
        with self.database.transaction() as connection:
            existing = self._by_idempotency(connection, idempotency_key)
            if existing is not None:
                if (
                    existing.request_artifact_id != request_artifact_id
                    or existing.request_hash != request_hash
                ):
                    raise PlantRunExecutionConflictError(
                        "Plant Run idempotency key was reused for another request"
                    )
                return existing
            artifact = connection.execute(
                "SELECT artifact_id FROM artifacts WHERE artifact_id = ?",
                (request_artifact_id,),
            ).fetchone()
            if artifact is None:
                raise PlantRunExecutionConflictError(
                    "Plant Run request Artifact does not exist"
                )
            info = PlantRunExecutionInfo(
                execution_id=f"pre_{uuid.uuid4().hex}",
                idempotency_key=idempotency_key,
                request_artifact_id=request_artifact_id,
                request_hash=request_hash,
                continuation_artifact_id=None,
                continuation_hash=None,
                status=PlantRunExecutionStatus.QUEUED,
                stage=PlantRunExecutionStage.GENERATION,
                revision=1,
                generation_job_id=None,
                generated_point_set_id=None,
                assignment_job_id=None,
                asset_assignment_set_id=None,
                elevation_job_id=None,
                elevation_point_set_id=None,
                export_job_id=None,
                export_id=None,
                error_code=None,
                error_message=None,
                created_at=created_at,
                updated_at=created_at,
            )
            connection.execute(
                """
                INSERT INTO plant_run_executions(
                    execution_id, idempotency_key, request_artifact_id,
                    request_hash, continuation_artifact_id,
                    continuation_hash, status, stage, revision,
                    generation_job_id, generated_point_set_id,
                    assignment_job_id, asset_assignment_set_id,
                    elevation_job_id, elevation_point_set_id,
                    export_job_id, export_id, error_code, error_message,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _values(info),
            )
            return info

    def get(self, execution_id: str) -> PlantRunExecutionInfo:
        row = self.database.connection.execute(
            _SELECT + " WHERE execution_id = ?",
            (execution_id,),
        ).fetchone()
        if row is None:
            raise PlantRunExecutionNotFoundError(execution_id)
        return _from_row(row)

    def get_by_idempotency(
        self,
        idempotency_key: str,
    ) -> PlantRunExecutionInfo | None:
        return self._by_idempotency(
            self.database.connection,
            idempotency_key,
        )

    def list_active(self, *, limit: int = 20) -> tuple[PlantRunExecutionInfo, ...]:
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        rows = self.database.connection.execute(
            _SELECT
            + " WHERE status IN ('queued', 'running', 'cancel_requested')"
            + " ORDER BY updated_at, execution_id LIMIT ?",
            (limit,),
        ).fetchall()
        return tuple(_from_row(row) for row in rows)

    def list_recent(self, *, limit: int = 20) -> tuple[PlantRunExecutionInfo, ...]:
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        rows = self.database.connection.execute(
            _SELECT + " ORDER BY updated_at DESC, execution_id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return tuple(_from_row(row) for row in rows)

    def update(
        self,
        info: PlantRunExecutionInfo,
        *,
        expected_revision: int,
        updated_at: str,
    ) -> PlantRunExecutionInfo:
        if expected_revision < 1:
            raise ValueError("expected_revision must be positive")
        updated = PlantRunExecutionInfo(
            execution_id=info.execution_id,
            idempotency_key=info.idempotency_key,
            request_artifact_id=info.request_artifact_id,
            request_hash=info.request_hash,
            continuation_artifact_id=info.continuation_artifact_id,
            continuation_hash=info.continuation_hash,
            status=info.status,
            stage=info.stage,
            revision=expected_revision + 1,
            generation_job_id=info.generation_job_id,
            generated_point_set_id=info.generated_point_set_id,
            assignment_job_id=info.assignment_job_id,
            asset_assignment_set_id=info.asset_assignment_set_id,
            elevation_job_id=info.elevation_job_id,
            elevation_point_set_id=info.elevation_point_set_id,
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
                raise PlantRunExecutionNotFoundError(info.execution_id)
            current_info = _from_row(current)
            immutable = (
                current_info.idempotency_key,
                current_info.request_artifact_id,
                current_info.request_hash,
                current_info.created_at,
            )
            proposed = (
                info.idempotency_key,
                info.request_artifact_id,
                info.request_hash,
                info.created_at,
            )
            if immutable != proposed:
                raise PlantRunExecutionConflictError(
                    "Plant Run execution immutable facts changed"
                )
            if (
                current_info.continuation_artifact_id is not None
                and (
                    info.continuation_artifact_id
                    != current_info.continuation_artifact_id
                    or info.continuation_hash != current_info.continuation_hash
                )
            ):
                raise PlantRunExecutionConflictError(
                    "Plant Run continuation cannot be replaced"
                )
            cursor = connection.execute(
                """
                UPDATE plant_run_executions
                SET status = ?, stage = ?, revision = ?,
                    continuation_artifact_id = ?, continuation_hash = ?,
                    generation_job_id = ?, generated_point_set_id = ?,
                    assignment_job_id = ?, asset_assignment_set_id = ?,
                    elevation_job_id = ?, elevation_point_set_id = ?,
                    export_job_id = ?, export_id = ?, error_code = ?,
                    error_message = ?, updated_at = ?
                WHERE execution_id = ? AND revision = ?
                """,
                (
                    updated.status.value,
                    updated.stage.value,
                    updated.revision,
                    updated.continuation_artifact_id,
                    updated.continuation_hash,
                    updated.generation_job_id,
                    updated.generated_point_set_id,
                    updated.assignment_job_id,
                    updated.asset_assignment_set_id,
                    updated.elevation_job_id,
                    updated.elevation_point_set_id,
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
                raise PlantRunExecutionConflictError(
                    "Plant Run execution revision is stale"
                )
        return updated

    @staticmethod
    def _by_idempotency(
        connection: sqlite3.Connection,
        idempotency_key: str,
    ) -> PlantRunExecutionInfo | None:
        row = connection.execute(
            _SELECT + " WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        return _from_row(row) if row is not None else None

    @staticmethod
    def _required(value: str, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} is required")
        return value.strip()


def _values(info: PlantRunExecutionInfo) -> tuple[object, ...]:
    return (
        info.execution_id,
        info.idempotency_key,
        info.request_artifact_id,
        info.request_hash,
        info.continuation_artifact_id,
        info.continuation_hash,
        info.status.value,
        info.stage.value,
        info.revision,
        info.generation_job_id,
        info.generated_point_set_id,
        info.assignment_job_id,
        info.asset_assignment_set_id,
        info.elevation_job_id,
        info.elevation_point_set_id,
        info.export_job_id,
        info.export_id,
        info.error_code,
        info.error_message,
        info.created_at,
        info.updated_at,
    )


def _from_row(row: object) -> PlantRunExecutionInfo:
    return PlantRunExecutionInfo(
        execution_id=row["execution_id"],
        idempotency_key=row["idempotency_key"],
        request_artifact_id=row["request_artifact_id"],
        request_hash=row["request_hash"],
        continuation_artifact_id=row["continuation_artifact_id"],
        continuation_hash=row["continuation_hash"],
        status=PlantRunExecutionStatus(row["status"]),
        stage=PlantRunExecutionStage(row["stage"]),
        revision=int(row["revision"]),
        generation_job_id=row["generation_job_id"],
        generated_point_set_id=row["generated_point_set_id"],
        assignment_job_id=row["assignment_job_id"],
        asset_assignment_set_id=row["asset_assignment_set_id"],
        elevation_job_id=row["elevation_job_id"],
        elevation_point_set_id=row["elevation_point_set_id"],
        export_job_id=row["export_job_id"],
        export_id=row["export_id"],
        error_code=row["error_code"],
        error_message=row["error_message"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
