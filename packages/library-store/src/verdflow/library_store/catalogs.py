"""Mutable current target asset Catalog persistence."""

from __future__ import annotations

import json
import sqlite3

from verdflow.contracts import (
    AssetCatalogDocument,
    AssetCatalogInfo,
    parse_library_resource_reference,
    require_package_hash,
)

from .database import LibraryDatabase
from .errors import (
    AssetCatalogConflictError,
    AssetCatalogNotFoundError,
    LibraryIntegrityError,
)
from .identities import stable_id


class AssetCatalogRepository:
    """Persist one mutable current document per target/catalog identity."""

    def __init__(self, database: LibraryDatabase) -> None:
        self._database = database

    def save(
        self,
        document: AssetCatalogDocument,
        *,
        expected_generation: int,
        changed_at: str,
        origin_package_hash: str | None = None,
    ) -> AssetCatalogInfo:
        if expected_generation < 0:
            raise ValueError("expected_generation must not be negative")
        if origin_package_hash is not None:
            require_package_hash(origin_package_hash)
        references = _resource_references(document)
        with self._database.transaction() as connection:
            current = self._find_row(
                document.target_type,
                document.catalog_id,
                connection=connection,
            )
            if current is None:
                if expected_generation != 0:
                    raise AssetCatalogConflictError(
                        "Catalog generation is stale"
                    )
                catalog_entry_id = stable_id(
                    "catalog",
                    f"{document.target_type}:{document.catalog_id}",
                )
                generation = 1
                created_at = changed_at
            else:
                if int(current["generation"]) != expected_generation:
                    raise AssetCatalogConflictError(
                        "Catalog generation is stale"
                    )
                _require_same_identity(current, document)
                if (
                    str(current["semantic_hash"]) == document.semantic_hash
                    and bytes(current["canonical_json"])
                    == document.canonical_json
                ):
                    return _to_info(current)
                catalog_entry_id = str(current["catalog_entry_id"])
                generation = expected_generation + 1
                created_at = str(current["created_at"])
                if origin_package_hash is None:
                    origin_package_hash = (
                        str(current["origin_package_hash"])
                        if current["origin_package_hash"] is not None
                        else None
                    )

            _require_resources(connection, references)
            connection.execute(
                """
                INSERT INTO asset_catalogs(
                    catalog_entry_id, target_type, catalog_id, provider_id,
                    display_name, generation, semantic_hash, canonical_json,
                    byte_size, created_at, updated_at, origin_package_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(target_type, catalog_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    generation = excluded.generation,
                    semantic_hash = excluded.semantic_hash,
                    canonical_json = excluded.canonical_json,
                    byte_size = excluded.byte_size,
                    updated_at = excluded.updated_at,
                    origin_package_hash = excluded.origin_package_hash
                """,
                (
                    catalog_entry_id,
                    document.target_type,
                    document.catalog_id,
                    document.provider_id,
                    document.display_name,
                    generation,
                    document.semantic_hash,
                    document.canonical_json,
                    len(document.canonical_json),
                    created_at,
                    changed_at,
                    origin_package_hash,
                ),
            )
            connection.execute(
                "DELETE FROM asset_catalog_resource_refs "
                "WHERE catalog_entry_id = ?",
                (catalog_entry_id,),
            )
            for asset_id, resource_id in references:
                connection.execute(
                    """
                    INSERT INTO asset_catalog_resource_refs(
                        catalog_entry_id, asset_id, resource_role, resource_id
                    ) VALUES (?, ?, 'thumbnail', ?)
                    """,
                    (catalog_entry_id, asset_id, resource_id),
                )
        return self.get(document.target_type, document.catalog_id)

    def get(self, target_type: str, catalog_id: str) -> AssetCatalogInfo:
        row = self._find_row(target_type, catalog_id)
        if row is None:
            raise AssetCatalogNotFoundError(f"{target_type}/{catalog_id}")
        return _to_info(row)

    def find(
        self,
        target_type: str,
        catalog_id: str,
    ) -> AssetCatalogInfo | None:
        row = self._find_row(target_type, catalog_id)
        return _to_info(row) if row is not None else None

    def list(
        self,
        target_type: str | None = None,
    ) -> tuple[AssetCatalogInfo, ...]:
        query = "SELECT * FROM asset_catalogs"
        parameters: tuple[str, ...] = ()
        if target_type is not None:
            query += " WHERE target_type = ?"
            parameters = (target_type,)
        query += " ORDER BY display_name, catalog_id"
        rows = self._database.connection.execute(query, parameters).fetchall()
        return tuple(_to_info(row) for row in rows)

    def load_document(
        self,
        target_type: str,
        catalog_id: str,
    ) -> AssetCatalogDocument:
        row = self._find_row(target_type, catalog_id)
        if row is None:
            raise AssetCatalogNotFoundError(f"{target_type}/{catalog_id}")
        try:
            return AssetCatalogDocument(
                target_type=str(row["target_type"]),
                provider_id=str(row["provider_id"]),
                catalog_id=str(row["catalog_id"]),
                display_name=str(row["display_name"]),
                semantic_hash=str(row["semantic_hash"]),
                canonical_json=bytes(row["canonical_json"]),
            )
        except ValueError as error:
            raise LibraryIntegrityError(
                "Stored Catalog bytes failed hash validation"
            ) from error

    def delete(self, target_type: str, catalog_id: str) -> int:
        """Delete the current Catalog without cascading into Profiles."""
        with self._database.transaction() as connection:
            row = self._find_row(
                target_type,
                catalog_id,
                connection=connection,
            )
            if row is None:
                raise AssetCatalogNotFoundError(f"{target_type}/{catalog_id}")
            affected_profiles = 0
            profiles = connection.execute(
                "SELECT catalog_id, canonical_json FROM role_binding_profiles WHERE target_type = ?",
                (target_type,),
            ).fetchall()
            for profile in profiles:
                document = json.loads(bytes(profile["canonical_json"]))
                if profile["catalog_id"] == catalog_id or any(
                    candidate.get("catalog_link", {}).get("catalog_id") == catalog_id
                    for assignment in document["assignments"].values()
                    for candidate in assignment["candidates"]
                ):
                    affected_profiles += 1
            connection.execute(
                "DELETE FROM asset_catalog_resource_refs "
                "WHERE catalog_entry_id = ?",
                (row["catalog_entry_id"],),
            )
            connection.execute(
                "DELETE FROM asset_catalogs WHERE catalog_entry_id = ?",
                (row["catalog_entry_id"],),
            )
            return affected_profiles

    def _find_row(
        self,
        target_type: str,
        catalog_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> sqlite3.Row | None:
        database = connection or self._database.connection
        return database.execute(
            "SELECT * FROM asset_catalogs "
            "WHERE target_type = ? AND catalog_id = ?",
            (target_type, catalog_id),
        ).fetchone()


def _to_info(row: sqlite3.Row) -> AssetCatalogInfo:
    return AssetCatalogInfo(
        catalog_entry_id=str(row["catalog_entry_id"]),
        target_type=str(row["target_type"]),
        provider_id=str(row["provider_id"]),
        catalog_id=str(row["catalog_id"]),
        display_name=str(row["display_name"]),
        generation=int(row["generation"]),
        semantic_hash=str(row["semantic_hash"]),
        byte_size=int(row["byte_size"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        origin_package_hash=(
            str(row["origin_package_hash"])
            if row["origin_package_hash"] is not None
            else None
        ),
    )


def _require_same_identity(
    row: sqlite3.Row,
    document: AssetCatalogDocument,
) -> None:
    if str(row["provider_id"]) != document.provider_id:
        raise AssetCatalogConflictError(
            "Catalog provider cannot change; create a new Catalog"
        )


def _resource_references(
    document: AssetCatalogDocument,
) -> tuple[tuple[str, str], ...]:
    try:
        payload = json.loads(document.canonical_json)
        assets = payload["assets"]
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise LibraryIntegrityError(
            "Catalog canonical bytes cannot be inspected for resources"
        ) from error
    references: list[tuple[str, str]] = []
    for asset in assets:
        thumbnail = asset.get("thumbnail")
        if thumbnail is None:
            continue
        try:
            resource_id = parse_library_resource_reference(thumbnail)
        except ValueError as error:
            raise LibraryIntegrityError(
                "Catalog contains an invalid managed resource reference"
            ) from error
        references.append((str(asset["asset_id"]), resource_id))
    return tuple(references)


def _require_resources(
    connection: sqlite3.Connection,
    references: tuple[tuple[str, str], ...],
) -> None:
    for asset_id, resource_id in references:
        resource = connection.execute(
            "SELECT 1 FROM library_resources WHERE resource_id = ?",
            (resource_id,),
        ).fetchone()
        if resource is None:
            raise LibraryIntegrityError(
                f"Catalog asset {asset_id!r} references missing "
                f"library resource {resource_id!r}"
            )
