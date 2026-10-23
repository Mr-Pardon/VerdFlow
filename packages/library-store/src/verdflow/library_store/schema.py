"""Versioned schema bootstrap and compatibility reads."""

from __future__ import annotations

import sqlite3

from .database import LibraryDatabase
from .errors import LibrarySchemaError


SCHEMA_VERSION = 9

PROFILE_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE role_binding_profiles (
        profile_entry_id TEXT PRIMARY KEY,
        profile_id TEXT NOT NULL UNIQUE,
        display_name TEXT NOT NULL,
        strategy_id TEXT NOT NULL,
        strategy_version TEXT NOT NULL,
        target_type TEXT NOT NULL,
        provider_id TEXT NOT NULL,
        catalog_id TEXT,
        generation INTEGER NOT NULL CHECK (generation > 0),
        semantic_hash TEXT NOT NULL,
        canonical_json BLOB NOT NULL,
        byte_size INTEGER NOT NULL CHECK (byte_size > 0),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX role_binding_profiles_target_idx "
    "ON role_binding_profiles(target_type, provider_id, strategy_id, strategy_version)",
)

RESOURCE_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE library_resources (
        resource_id TEXT PRIMARY KEY,
        content_hash TEXT NOT NULL UNIQUE,
        media_type TEXT NOT NULL,
        byte_size INTEGER NOT NULL CHECK (byte_size > 0),
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE asset_catalog_resource_refs (
        catalog_entry_id TEXT NOT NULL
            REFERENCES asset_catalogs(catalog_entry_id) ON DELETE CASCADE,
        asset_id TEXT NOT NULL,
        resource_role TEXT NOT NULL,
        resource_id TEXT NOT NULL
            REFERENCES library_resources(resource_id)
            ON DELETE RESTRICT,
        PRIMARY KEY (catalog_entry_id, asset_id, resource_role)
    )
    """,
    "CREATE INDEX asset_catalog_resource_refs_resource_idx "
    "ON asset_catalog_resource_refs(resource_id)",
)

CATALOG_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE asset_catalogs (
        catalog_entry_id TEXT PRIMARY KEY,
        target_type TEXT NOT NULL,
        catalog_id TEXT NOT NULL,
        provider_id TEXT NOT NULL,
        display_name TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK (generation > 0),
        semantic_hash TEXT NOT NULL,
        canonical_json BLOB NOT NULL,
        byte_size INTEGER NOT NULL CHECK (byte_size > 0),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        origin_package_hash TEXT,
        UNIQUE (target_type, catalog_id)
    )
    """,
    "CREATE INDEX asset_catalogs_target_idx "
    "ON asset_catalogs(target_type, display_name, catalog_id)",
)

SCHEMA_STATEMENTS = (
    """
    CREATE TABLE schema_metadata (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE strategy_packages (
        library_entry_id TEXT PRIMARY KEY,
        package_id TEXT NOT NULL,
        package_version TEXT NOT NULL,
        package_hash TEXT NOT NULL UNIQUE,
        display_name TEXT NOT NULL,
        payload_hash TEXT NOT NULL,
        install_relpath TEXT NOT NULL UNIQUE,
        installed_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX strategy_packages_id_version_idx "
    "ON strategy_packages(package_id, package_version)",
    """
    CREATE TABLE strategy_trust_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        package_hash TEXT NOT NULL
            REFERENCES strategy_packages(package_hash) ON DELETE RESTRICT,
        state TEXT NOT NULL CHECK (state IN ('trusted', 'revoked')),
        decided_at TEXT NOT NULL,
        decided_by TEXT NOT NULL,
        reason TEXT
    )
    """,
    "CREATE INDEX strategy_trust_events_current_idx "
    "ON strategy_trust_events(package_hash, event_id DESC)",
    *CATALOG_SCHEMA_STATEMENTS,
    *RESOURCE_SCHEMA_STATEMENTS,
    *PROFILE_SCHEMA_STATEMENTS,
)


def initialize_schema(database: LibraryDatabase) -> None:
    with database.transaction() as connection:
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        connection.execute(
            "INSERT INTO schema_metadata(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )


def read_schema_version(database: LibraryDatabase) -> int:
    try:
        row = database.connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
        ).fetchone()
    except sqlite3.DatabaseError as error:
        raise LibrarySchemaError("Library database is malformed") from error
    if row is None:
        raise LibrarySchemaError("Library schema version is missing")
    try:
        return int(row["value"])
    except (TypeError, ValueError) as error:
        raise LibrarySchemaError("Library schema version is invalid") from error


def validate_schema(database: LibraryDatabase) -> None:
    version = read_schema_version(database)
    if version != SCHEMA_VERSION:
        raise LibrarySchemaError(
            f"Unsupported library schema {version}; expected {SCHEMA_VERSION}"
        )
