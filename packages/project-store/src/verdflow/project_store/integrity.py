"""Project database and referenced-object integrity validation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .database import ProjectDatabase
from .errors import ProjectIntegrityError
from .layout import ProjectLayout


@dataclass(frozen=True, slots=True)
class IntegrityReport:
    checked_object_count: int
    verified_content_hash_count: int


class ProjectIntegrityChecker:
    def __init__(
        self,
        database: ProjectDatabase,
        layout: ProjectLayout,
    ) -> None:
        self.database = database
        self.layout = layout

    def verify(
        self,
        *,
        verify_content_hashes: bool = False,
    ) -> IntegrityReport:
        database_errors = self.database.integrity_errors()
        if database_errors:
            raise ProjectIntegrityError(
                "SQLite integrity check failed: " + "; ".join(database_errors)
            )

        foreign_key_errors = self.database.connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()
        if foreign_key_errors:
            raise ProjectIntegrityError(
                f"SQLite foreign key check found {len(foreign_key_errors)} error(s)"
            )

        self._verify_workflow_state()

        checked = 0
        verified_hashes = 0
        rows = self.database.connection.execute(
            "SELECT content_hash, relative_path, byte_size FROM objects"
        )
        for row in rows:
            content_hash = str(row["content_hash"])
            expected_relative = (
                f"objects/{content_hash[:2]}/{content_hash}.blob"
            )
            if row["relative_path"] != expected_relative:
                raise ProjectIntegrityError(
                    f"Object {content_hash} has an unexpected storage path"
                )
            path = self.layout.root / expected_relative
            if not path.is_file():
                raise ProjectIntegrityError(
                    f"Referenced object is missing: {content_hash}"
                )
            if path.stat().st_size != int(row["byte_size"]):
                raise ProjectIntegrityError(
                    f"Referenced object has the wrong byte size: {content_hash}"
                )
            if verify_content_hashes:
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    while chunk := stream.read(8 * 1024 * 1024):
                        digest.update(chunk)
                if digest.hexdigest() != content_hash:
                    raise ProjectIntegrityError(
                        f"Referenced object content hash is invalid: {content_hash}"
                    )
                verified_hashes += 1
            checked += 1
        return IntegrityReport(
            checked_object_count=checked,
            verified_content_hash_count=verified_hashes,
        )

    def _verify_workflow_state(self) -> None:
        rows = self.database.connection.execute(
            "SELECT * FROM project_workflow_state"
        ).fetchall()
        if len(rows) != 1:
            raise ProjectIntegrityError(
                "Project must contain exactly one workflow state"
            )
        row = rows[0]
        project = self.database.connection.execute(
            "SELECT project_id FROM projects LIMIT 1"
        ).fetchone()
        if project is None or row["project_id"] != project["project_id"]:
            raise ProjectIntegrityError(
                "Project workflow state does not belong to the project"
            )
        references = (
            ("source_id", "sources", "source_id"),
            (
                "scene_preparation_execution_id",
                "scene_preparation_executions",
                "execution_id",
            ),
            (
                "material_flow_execution_id",
                "material_flow_executions",
                "execution_id",
            ),
            ("region_set_id", "region_sets", "region_set_id"),
            (
                "plant_run_execution_id",
                "plant_run_executions",
                "execution_id",
            ),
        )
        for field, table, identity in references:
            value = row[field]
            if value is None:
                continue
            found = self.database.connection.execute(
                f"SELECT 1 FROM {table} WHERE {identity} = ?",
                (value,),
            ).fetchone()
            if found is None:
                raise ProjectIntegrityError(
                    f"Project workflow {field} does not exist: {value}"
                )
