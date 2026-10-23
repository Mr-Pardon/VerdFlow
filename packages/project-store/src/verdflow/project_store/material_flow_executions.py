"""Persistent coordinator state for material analysis through extraction."""

from __future__ import annotations

import sqlite3
import uuid

from verdflow.contracts import (
    MaterialFlowExecutionInfo,
    MaterialFlowExecutionStage,
    MaterialFlowExecutionStatus,
)

from .database import ProjectDatabase
from .errors import (
    MaterialFlowExecutionConflictError,
    MaterialFlowExecutionNotFoundError,
)


_SELECT = """
SELECT execution_id, idempotency_key, request_artifact_id, request_hash,
       status, stage, revision, analysis_job_id, rule_pack_artifact_id,
       extraction_job_id, region_set_id, error_code, error_message,
       created_at, updated_at
FROM material_flow_executions
"""


class MaterialFlowExecutionRepository:
    """Persist state without interpreting material-flow policy."""

    def __init__(self, database: ProjectDatabase) -> None:
        self.database = database

    def create(
        self,
        *,
        idempotency_key: str,
        request_artifact_id: str,
        request_hash: str,
        created_at: str,
    ) -> MaterialFlowExecutionInfo:
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
                    raise MaterialFlowExecutionConflictError(
                        "Material Flow idempotency key was reused for another request"
                    )
                return existing
            artifact = connection.execute(
                "SELECT artifact_id FROM artifacts WHERE artifact_id = ?",
                (request_artifact_id,),
            ).fetchone()
            if artifact is None:
                raise MaterialFlowExecutionConflictError(
                    "Material Flow request Artifact does not exist"
                )
            info = MaterialFlowExecutionInfo(
                execution_id=f"mfe_{uuid.uuid4().hex}",
                idempotency_key=idempotency_key,
                request_artifact_id=request_artifact_id,
                request_hash=request_hash,
                status=MaterialFlowExecutionStatus.QUEUED,
                stage=MaterialFlowExecutionStage.ANALYSIS,
                revision=1,
                analysis_job_id=None,
                rule_pack_artifact_id=None,
                extraction_job_id=None,
                region_set_id=None,
                error_code=None,
                error_message=None,
                created_at=created_at,
                updated_at=created_at,
            )
            connection.execute(
                """
                INSERT INTO material_flow_executions(
                    execution_id, idempotency_key, request_artifact_id,
                    request_hash, status, stage, revision, analysis_job_id,
                    rule_pack_artifact_id, extraction_job_id, region_set_id,
                    error_code, error_message, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _values(info),
            )
            return info

    def get(self, execution_id: str) -> MaterialFlowExecutionInfo:
        row = self.database.connection.execute(
            _SELECT + " WHERE execution_id = ?",
            (execution_id,),
        ).fetchone()
        if row is None:
            raise MaterialFlowExecutionNotFoundError(execution_id)
        return _from_row(row)

    def get_by_idempotency(
        self,
        idempotency_key: str,
    ) -> MaterialFlowExecutionInfo | None:
        return self._by_idempotency(self.database.connection, idempotency_key)

    def list_active(self, *, limit: int = 20) -> tuple[MaterialFlowExecutionInfo, ...]:
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        rows = self.database.connection.execute(
            _SELECT
            + " WHERE status IN ('queued', 'running', 'cancel_requested')"
            + " ORDER BY updated_at, execution_id LIMIT ?",
            (limit,),
        ).fetchall()
        return tuple(_from_row(row) for row in rows)

    def list_recent(self, *, limit: int = 20) -> tuple[MaterialFlowExecutionInfo, ...]:
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        rows = self.database.connection.execute(
            _SELECT + " ORDER BY updated_at DESC, execution_id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return tuple(_from_row(row) for row in rows)

    def update(
        self,
        info: MaterialFlowExecutionInfo,
        *,
        expected_revision: int,
        updated_at: str,
    ) -> MaterialFlowExecutionInfo:
        if expected_revision < 1:
            raise ValueError("expected_revision must be positive")
        updated = MaterialFlowExecutionInfo(
            execution_id=info.execution_id,
            idempotency_key=info.idempotency_key,
            request_artifact_id=info.request_artifact_id,
            request_hash=info.request_hash,
            status=info.status,
            stage=info.stage,
            revision=expected_revision + 1,
            analysis_job_id=info.analysis_job_id,
            rule_pack_artifact_id=info.rule_pack_artifact_id,
            extraction_job_id=info.extraction_job_id,
            region_set_id=info.region_set_id,
            error_code=info.error_code,
            error_message=info.error_message,
            created_at=info.created_at,
            updated_at=updated_at,
        )
        with self.database.transaction() as connection:
            row = connection.execute(
                _SELECT + " WHERE execution_id = ?",
                (info.execution_id,),
            ).fetchone()
            if row is None:
                raise MaterialFlowExecutionNotFoundError(info.execution_id)
            current = _from_row(row)
            if (
                current.idempotency_key,
                current.request_artifact_id,
                current.request_hash,
                current.created_at,
            ) != (
                info.idempotency_key,
                info.request_artifact_id,
                info.request_hash,
                info.created_at,
            ):
                raise MaterialFlowExecutionConflictError(
                    "Material Flow execution immutable facts changed"
                )
            cursor = connection.execute(
                """
                UPDATE material_flow_executions
                SET status = ?, stage = ?, revision = ?, analysis_job_id = ?,
                    rule_pack_artifact_id = ?, extraction_job_id = ?,
                    region_set_id = ?, error_code = ?, error_message = ?,
                    updated_at = ?
                WHERE execution_id = ? AND revision = ?
                """,
                (
                    updated.status.value,
                    updated.stage.value,
                    updated.revision,
                    updated.analysis_job_id,
                    updated.rule_pack_artifact_id,
                    updated.extraction_job_id,
                    updated.region_set_id,
                    updated.error_code,
                    updated.error_message,
                    updated.updated_at,
                    updated.execution_id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise MaterialFlowExecutionConflictError(
                    "Material Flow execution revision is stale"
                )
        return updated

    @staticmethod
    def _by_idempotency(
        connection: sqlite3.Connection,
        idempotency_key: str,
    ) -> MaterialFlowExecutionInfo | None:
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


def _values(info: MaterialFlowExecutionInfo) -> tuple[object, ...]:
    return (
        info.execution_id,
        info.idempotency_key,
        info.request_artifact_id,
        info.request_hash,
        info.status.value,
        info.stage.value,
        info.revision,
        info.analysis_job_id,
        info.rule_pack_artifact_id,
        info.extraction_job_id,
        info.region_set_id,
        info.error_code,
        info.error_message,
        info.created_at,
        info.updated_at,
    )


def _from_row(row: object) -> MaterialFlowExecutionInfo:
    return MaterialFlowExecutionInfo(
        execution_id=row["execution_id"],
        idempotency_key=row["idempotency_key"],
        request_artifact_id=row["request_artifact_id"],
        request_hash=row["request_hash"],
        status=MaterialFlowExecutionStatus(row["status"]),
        stage=MaterialFlowExecutionStage(row["stage"]),
        revision=int(row["revision"]),
        analysis_job_id=row["analysis_job_id"],
        rule_pack_artifact_id=row["rule_pack_artifact_id"],
        extraction_job_id=row["extraction_job_id"],
        region_set_id=row["region_set_id"],
        error_code=row["error_code"],
        error_message=row["error_message"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
