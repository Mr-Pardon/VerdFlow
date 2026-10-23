"""Current-only LibraryStore schema gate.

VerdFlow has not published a Library format yet. Development databases from
earlier Profile models must be recreated instead of teaching the runtime how
to read or transform those abandoned contracts.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .database import LibraryDatabase
from .errors import LibraryMigrationError, LibrarySchemaError
from .schema import SCHEMA_VERSION, read_schema_version, validate_schema


@dataclass(frozen=True, slots=True)
class MigrationResult:
    source_version: int
    target_version: int
    migrated: bool


def migrate_to_current(
    database: LibraryDatabase,
    backup_directory: Path,
) -> MigrationResult:
    source_version = read_schema_version(database)
    if source_version > SCHEMA_VERSION:
        raise LibrarySchemaError(
            f"Library schema {source_version} is newer than supported "
            f"schema {SCHEMA_VERSION}"
        )
    if source_version != SCHEMA_VERSION:
        raise LibraryMigrationError(
            f"Library schema {source_version} is a pre-release test format; "
            "recreate the library with the current VerdFlow build"
        )
    del backup_directory
    validate_schema(database)
    return MigrationResult(source_version, SCHEMA_VERSION, False)


def cleanup_completed_migration_backups(directory: Path) -> int:
    removed = 0
    for path in directory.glob("library-v*-to-v*-*.db"):
        path.unlink()
        removed += 1
    return removed
