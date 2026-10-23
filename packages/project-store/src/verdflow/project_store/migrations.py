"""ProjectStore schema gate with narrowly scoped internal migrations.

VerdFlow has not published a project format yet. Development projects from
earlier Profile and snapshot models must be recreated instead of teaching the
runtime how to read abandoned pre-release contracts. Schemas 19 through 22 are
accepted only for the active Studio baseline: schemas 20 and 21 add composition
facts and durable XML history, while schema 22 corrects deferred elevation
completion for composition batches. Schema 23 admits self-contained unsaved
binding sources without changing existing snapshot identities.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import sqlite3

from .database import ProjectDatabase
from .errors import ProjectMigrationError, ProjectSchemaError
from .schema import (
    COMPOSITION_OUTPUT_EXECUTION_SCHEMA_STATEMENTS,
    PLANT_RUN_EXECUTION_SCHEMA_STATEMENTS,
    PLANTING_COMPOSITION_SCHEMA_STATEMENTS,
    SCHEMA_VERSION,
    SNAPSHOT_SCHEMA_STATEMENTS,
    read_schema_version,
    validate_schema,
)


@dataclass(frozen=True, slots=True)
class MigrationResult:
    source_version: int
    target_version: int
    migrated: bool


def migrate_to_current(
    database: ProjectDatabase,
    backup_directory: Path,
) -> MigrationResult:
    source_version = read_schema_version(database)
    if source_version > SCHEMA_VERSION:
        raise ProjectSchemaError(
            f"Project schema {source_version} is newer than supported "
            f"schema {SCHEMA_VERSION}"
        )
    if source_version in {19, 20, 21, 22} and SCHEMA_VERSION == 23:
        backup_directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        database.backup_to(
            backup_directory
            / f"project-v{source_version}-to-v{SCHEMA_VERSION}-{stamp}.db"
        )
        connection = database.connection
        connection.execute("PRAGMA foreign_keys = OFF")
        try:
            with database.transaction() as connection:
                if source_version == 19:
                    for statement in PLANTING_COMPOSITION_SCHEMA_STATEMENTS:
                        connection.execute(statement)
                elif source_version == 20:
                    _migrate_v20_composition_outputs(connection)
                if source_version <= 21:
                    _migrate_v21_plant_run_elevation_completion(connection)
                _migrate_v22_binding_draft_sources(connection)
                connection.execute(
                    "UPDATE schema_metadata SET value = ? "
                    "WHERE key = 'schema_version'",
                    (str(SCHEMA_VERSION),),
                )
                connection.execute(
                    "UPDATE projects SET schema_version = ?",
                    (SCHEMA_VERSION,),
                )
        finally:
            connection.execute("PRAGMA foreign_keys = ON")
        foreign_key_errors = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()
        if foreign_key_errors:
            raise ProjectMigrationError(
                "Project migration introduced invalid foreign-key references"
            )
        validate_schema(database)
        return MigrationResult(source_version, SCHEMA_VERSION, True)
    if source_version != SCHEMA_VERSION:
        raise ProjectMigrationError(
            f"Project schema {source_version} is a pre-release test format; "
            "recreate the project with the current VerdFlow build"
        )
    del backup_directory
    validate_schema(database)
    return MigrationResult(source_version, SCHEMA_VERSION, False)


def _migrate_v22_binding_draft_sources(connection: sqlite3.Connection) -> None:
    statement = next(
        item for item in SNAPSHOT_SCHEMA_STATEMENTS
        if "CREATE TABLE target_asset_use_snapshots" in item
    )
    connection.execute(statement.replace(
        "CREATE TABLE target_asset_use_snapshots", "CREATE TABLE target_asset_use_snapshots_v23",
    ))
    connection.execute(
        "INSERT INTO target_asset_use_snapshots_v23 SELECT * FROM target_asset_use_snapshots"
    )
    connection.execute("DROP TABLE target_asset_use_snapshots")
    connection.execute("ALTER TABLE target_asset_use_snapshots_v23 RENAME TO target_asset_use_snapshots")
    connection.execute(
        "CREATE INDEX target_asset_use_source_idx "
        "ON target_asset_use_snapshots(target_type, source_kind, source_id)"
    )


def _migrate_v20_composition_outputs(
    connection: sqlite3.Connection,
) -> None:
    connection.execute(
        """
        CREATE TABLE composition_exports_v21 (
            export_id TEXT PRIMARY KEY,
            composition_id TEXT NOT NULL,
            composition_generation INTEGER NOT NULL
                CHECK (composition_generation >= 1),
            producer_job_id TEXT NOT NULL UNIQUE REFERENCES jobs(job_id),
            adapter_id TEXT NOT NULL,
            adapter_version TEXT NOT NULL,
            target_type TEXT NOT NULL,
            output_artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
            offset_x REAL NOT NULL,
            offset_y REAL NOT NULL,
            offset_z REAL NOT NULL,
            record_count INTEGER NOT NULL CHECK (record_count >= 0),
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        INSERT INTO composition_exports_v21
        SELECT export_id, composition_id, composition_generation,
               producer_job_id, adapter_id, adapter_version, target_type,
               output_artifact_id, offset_x, offset_y, offset_z,
               record_count, created_at
        FROM composition_exports
        """
    )
    connection.execute(
        """
        CREATE TABLE composition_export_inputs_v21 (
            export_id TEXT NOT NULL
                REFERENCES composition_exports_v21(export_id) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
            batch_id TEXT NOT NULL,
            point_set_id TEXT NOT NULL REFERENCES point_sets(point_set_id),
            asset_assignment_set_id TEXT NOT NULL
                REFERENCES asset_assignment_sets(asset_assignment_set_id),
            selected_asset_ids_json TEXT,
            PRIMARY KEY (export_id, ordinal),
            UNIQUE (export_id, batch_id)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO composition_export_inputs_v21
        SELECT export_id, ordinal, batch_id, point_set_id,
               asset_assignment_set_id, selected_asset_ids_json
        FROM composition_export_inputs
        """
    )
    connection.execute("DROP TABLE composition_export_inputs")
    connection.execute("DROP TABLE composition_exports")
    connection.execute(
        "ALTER TABLE composition_exports_v21 RENAME TO composition_exports"
    )
    connection.execute(
        "ALTER TABLE composition_export_inputs_v21 "
        "RENAME TO composition_export_inputs"
    )
    connection.execute(
        "CREATE INDEX composition_exports_composition_idx "
        "ON composition_exports(composition_id, created_at)"
    )
    for statement in COMPOSITION_OUTPUT_EXECUTION_SCHEMA_STATEMENTS:
        connection.execute(statement)


def _migrate_v21_plant_run_elevation_completion(
    connection: sqlite3.Connection,
) -> None:
    table_statement = PLANT_RUN_EXECUTION_SCHEMA_STATEMENTS[0].replace(
        "CREATE TABLE plant_run_executions",
        "CREATE TABLE plant_run_executions_v22",
        1,
    )
    connection.execute(table_statement)
    connection.execute(
        """
        INSERT INTO plant_run_executions_v22
        SELECT * FROM plant_run_executions
        """
    )
    connection.execute(
        """
        UPDATE plant_run_executions_v22
        SET status = 'succeeded',
            stage = 'completed',
            revision = revision + 1,
            elevation_point_set_id = (
                SELECT point_set_id
                FROM point_sets
                WHERE producer_job_id = elevation_job_id
                ORDER BY created_at DESC, point_set_id DESC
                LIMIT 1
            ),
            error_code = NULL,
            error_message = NULL
        WHERE status = 'failed'
          AND stage = 'elevation'
          AND elevation_point_set_id IS NULL
          AND export_id IS NULL
          AND error_code = 'IntegrityError'
          AND error_message LIKE 'CHECK constraint failed:%export_id IS NOT NULL%'
          AND EXISTS (
              SELECT 1 FROM jobs
              WHERE job_id = elevation_job_id
                AND status = 'succeeded'
          )
          AND EXISTS (
              SELECT 1 FROM point_sets
              WHERE producer_job_id = elevation_job_id
          )
        """
    )
    connection.execute("DROP TABLE plant_run_executions")
    connection.execute(
        "ALTER TABLE plant_run_executions_v22 RENAME TO plant_run_executions"
    )
    connection.execute(PLANT_RUN_EXECUTION_SCHEMA_STATEMENTS[1])


def cleanup_completed_migration_backups(directory: Path) -> int:
    removed = 0
    for path in directory.glob("project-v*-to-v*-*.db"):
        path.unlink()
        removed += 1
    return removed
