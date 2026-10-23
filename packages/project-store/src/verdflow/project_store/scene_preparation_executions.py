"""Persistent product workflow state for Source-to-Region preparation."""

from __future__ import annotations

import sqlite3
import uuid

from verdflow.contracts import (
    ScenePreparationInfo,
    ScenePreparationStage,
    ScenePreparationStatus,
)

from .database import ProjectDatabase
from .errors import (
    ScenePreparationConflictError,
    ScenePreparationNotFoundError,
)


_SELECT = """
SELECT execution_id, idempotency_key, request_artifact_id, request_hash,
       status, stage, revision, source_id, inspection_job_id,
       scene_revision_id, material_flow_execution_id, region_set_id,
       error_code, error_message, created_at, updated_at
FROM scene_preparation_executions
"""


class ScenePreparationRepository:
    """Persist workflow state without running inspection or inference."""

    def __init__(self, database: ProjectDatabase) -> None:
        self.database = database

    def create(
        self,
        *,
        idempotency_key: str,
        request_artifact_id: str,
        request_hash: str,
        source_id: str,
        created_at: str,
    ) -> ScenePreparationInfo:
        values = {
            "idempotency_key": self._required(idempotency_key, "idempotency_key"),
            "request_artifact_id": self._required(
                request_artifact_id, "request_artifact_id"
            ),
            "request_hash": self._required(request_hash, "request_hash"),
            "source_id": self._required(source_id, "source_id"),
        }
        with self.database.transaction() as connection:
            existing = self._by_idempotency(
                connection,
                values["idempotency_key"],
            )
            if existing is not None:
                if (
                    existing.request_artifact_id != values["request_artifact_id"]
                    or existing.request_hash != values["request_hash"]
                    or existing.source_id != values["source_id"]
                ):
                    raise ScenePreparationConflictError(
                        "Scene Preparation idempotency key was reused"
                    )
                return existing
            if connection.execute(
                "SELECT artifact_id FROM artifacts WHERE artifact_id = ?",
                (values["request_artifact_id"],),
            ).fetchone() is None:
                raise ScenePreparationConflictError(
                    "Scene Preparation request Artifact does not exist"
                )
            if connection.execute(
                "SELECT source_id FROM sources WHERE source_id = ?",
                (values["source_id"],),
            ).fetchone() is None:
                raise ScenePreparationConflictError("Source does not exist")
            info = ScenePreparationInfo(
                execution_id=f"spe_{uuid.uuid4().hex}",
                idempotency_key=values["idempotency_key"],
                request_artifact_id=values["request_artifact_id"],
                request_hash=values["request_hash"],
                status=ScenePreparationStatus.QUEUED,
                stage=ScenePreparationStage.INSPECTION,
                revision=1,
                source_id=values["source_id"],
                inspection_job_id=None,
                scene_revision_id=None,
                material_flow_execution_id=None,
                region_set_id=None,
                error_code=None,
                error_message=None,
                created_at=created_at,
                updated_at=created_at,
            )
            connection.execute(
                """
                INSERT INTO scene_preparation_executions(
                    execution_id, idempotency_key, request_artifact_id,
                    request_hash, status, stage, revision, source_id,
                    inspection_job_id, scene_revision_id,
                    material_flow_execution_id, region_set_id, error_code,
                    error_message, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _values(info),
            )
            return info

    def get(self, execution_id: str) -> ScenePreparationInfo:
        row = self.database.connection.execute(
            _SELECT + " WHERE execution_id = ?",
            (execution_id,),
        ).fetchone()
        if row is None:
            raise ScenePreparationNotFoundError(execution_id)
        return _from_row(row)

    def get_by_idempotency(self, idempotency_key: str) -> ScenePreparationInfo | None:
        return self._by_idempotency(self.database.connection, idempotency_key)

    def list_active(self, *, limit: int = 20) -> tuple[ScenePreparationInfo, ...]:
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        rows = self.database.connection.execute(
            _SELECT
            + " WHERE status IN ('queued', 'running', 'cancel_requested')"
            + " ORDER BY updated_at, execution_id LIMIT ?",
            (limit,),
        ).fetchall()
        return tuple(_from_row(row) for row in rows)

    def list_recent(self, *, limit: int = 20) -> tuple[ScenePreparationInfo, ...]:
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        rows = self.database.connection.execute(
            _SELECT + " ORDER BY updated_at DESC, execution_id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return tuple(_from_row(row) for row in rows)

    def update(
        self,
        info: ScenePreparationInfo,
        *,
        expected_revision: int,
        updated_at: str,
    ) -> ScenePreparationInfo:
        if expected_revision < 1:
            raise ValueError("expected_revision must be positive")
        updated = ScenePreparationInfo(
            execution_id=info.execution_id,
            idempotency_key=info.idempotency_key,
            request_artifact_id=info.request_artifact_id,
            request_hash=info.request_hash,
            status=info.status,
            stage=info.stage,
            revision=expected_revision + 1,
            source_id=info.source_id,
            inspection_job_id=info.inspection_job_id,
            scene_revision_id=info.scene_revision_id,
            material_flow_execution_id=info.material_flow_execution_id,
            region_set_id=info.region_set_id,
            error_code=info.error_code,
            error_message=info.error_message,
            created_at=info.created_at,
            updated_at=updated_at,
        )
        with self.database.transaction() as connection:
            current_row = connection.execute(
                _SELECT + " WHERE execution_id = ?",
                (info.execution_id,),
            ).fetchone()
            if current_row is None:
                raise ScenePreparationNotFoundError(info.execution_id)
            current = _from_row(current_row)
            if (
                current.idempotency_key,
                current.request_artifact_id,
                current.request_hash,
                current.source_id,
                current.created_at,
            ) != (
                info.idempotency_key,
                info.request_artifact_id,
                info.request_hash,
                info.source_id,
                info.created_at,
            ):
                raise ScenePreparationConflictError(
                    "Scene Preparation immutable facts changed"
                )
            cursor = connection.execute(
                """
                UPDATE scene_preparation_executions
                SET status = ?, stage = ?, revision = ?, inspection_job_id = ?,
                    scene_revision_id = ?, material_flow_execution_id = ?,
                    region_set_id = ?, error_code = ?, error_message = ?,
                    updated_at = ?
                WHERE execution_id = ? AND revision = ?
                """,
                (
                    updated.status.value,
                    updated.stage.value,
                    updated.revision,
                    updated.inspection_job_id,
                    updated.scene_revision_id,
                    updated.material_flow_execution_id,
                    updated.region_set_id,
                    updated.error_code,
                    updated.error_message,
                    updated.updated_at,
                    updated.execution_id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise ScenePreparationConflictError(
                    "Scene Preparation revision is stale"
                )
        return updated

    @staticmethod
    def _by_idempotency(
        connection: sqlite3.Connection,
        idempotency_key: str,
    ) -> ScenePreparationInfo | None:
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


def _values(info: ScenePreparationInfo) -> tuple[object, ...]:
    return (
        info.execution_id,
        info.idempotency_key,
        info.request_artifact_id,
        info.request_hash,
        info.status.value,
        info.stage.value,
        info.revision,
        info.source_id,
        info.inspection_job_id,
        info.scene_revision_id,
        info.material_flow_execution_id,
        info.region_set_id,
        info.error_code,
        info.error_message,
        info.created_at,
        info.updated_at,
    )


def _from_row(row: object) -> ScenePreparationInfo:
    return ScenePreparationInfo(
        execution_id=row["execution_id"],
        idempotency_key=row["idempotency_key"],
        request_artifact_id=row["request_artifact_id"],
        request_hash=row["request_hash"],
        status=ScenePreparationStatus(row["status"]),
        stage=ScenePreparationStage(row["stage"]),
        revision=int(row["revision"]),
        source_id=row["source_id"],
        inspection_job_id=row["inspection_job_id"],
        scene_revision_id=row["scene_revision_id"],
        material_flow_execution_id=row["material_flow_execution_id"],
        region_set_id=row["region_set_id"],
        error_code=row["error_code"],
        error_message=row["error_message"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
