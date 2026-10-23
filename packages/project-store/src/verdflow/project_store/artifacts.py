"""Artifact metadata repository."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime

from verdflow.contracts import ArtifactRef

from .database import ProjectDatabase
from .errors import ArtifactNotFoundError
from .object_store import StoredObject


EMPTY_SCHEMA_VALUE = ""


def normalize_schema_value(value: str | None) -> str:
    return value or EMPTY_SCHEMA_VALUE


def artifact_identity(
    content_hash: str,
    media_type: str,
    schema_id: str,
    schema_version: str,
) -> str:
    identity = "\0".join(
        (content_hash, media_type, schema_id, schema_version)
    )
    return f"art_{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"


class ArtifactRepository:
    def __init__(self, database: ProjectDatabase) -> None:
        self.database = database

    def register(
        self,
        stored_object: StoredObject,
        *,
        media_type: str,
        schema_id: str | None,
        schema_version: str | None,
    ) -> ArtifactRef:
        normalized_schema_id = normalize_schema_value(schema_id)
        normalized_schema_version = normalize_schema_value(schema_version)
        artifact_id = artifact_identity(
            stored_object.content_hash,
            media_type,
            normalized_schema_id,
            normalized_schema_version,
        )
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO objects(
                    content_hash, relative_path, byte_size, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    stored_object.content_hash,
                    stored_object.relative_path,
                    stored_object.byte_size,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO artifacts(
                    artifact_id, content_hash, media_type, schema_id,
                    schema_version, byte_size, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact_id,
                    stored_object.content_hash,
                    media_type,
                    normalized_schema_id,
                    normalized_schema_version,
                    stored_object.byte_size,
                    now,
                ),
            )
        return self.get(artifact_id)

    def get(self, artifact_id: str) -> ArtifactRef:
        row = self.database.connection.execute(
            """
            SELECT artifact_id, content_hash, media_type, byte_size,
                   schema_id, schema_version
            FROM artifacts
            WHERE artifact_id = ?
            """,
            (artifact_id,),
        ).fetchone()
        if row is None:
            raise ArtifactNotFoundError(artifact_id)
        return self._from_row(row)

    def delete(self, artifact_id: str) -> None:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM artifacts WHERE artifact_id = ?",
                (artifact_id,),
            )
            if cursor.rowcount == 0:
                raise ArtifactNotFoundError(artifact_id)

    def referenced_hashes(self) -> set[str]:
        return {
            row["content_hash"]
            for row in self.database.connection.execute(
                "SELECT DISTINCT content_hash FROM artifacts"
            )
        }

    def delete_unreferenced_object_records(self) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                DELETE FROM objects
                WHERE content_hash NOT IN (
                    SELECT DISTINCT content_hash FROM artifacts
                )
                """
            )

    def object_count(self) -> int:
        row = self.database.connection.execute(
            "SELECT COUNT(*) AS count FROM objects"
        ).fetchone()
        return int(row["count"])

    def artifact_count(self) -> int:
        row = self.database.connection.execute(
            "SELECT COUNT(*) AS count FROM artifacts"
        ).fetchone()
        return int(row["count"])

    @staticmethod
    def _from_row(row: sqlite3.Row) -> ArtifactRef:
        return ArtifactRef(
            artifact_id=row["artifact_id"],
            content_hash=row["content_hash"],
            media_type=row["media_type"],
            byte_size=row["byte_size"],
            schema_id=row["schema_id"] or None,
            schema_version=row["schema_version"] or None,
        )

