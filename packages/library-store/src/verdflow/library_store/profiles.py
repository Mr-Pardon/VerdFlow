"""Mutable self-contained Profile persistence."""

from __future__ import annotations

import sqlite3

from verdflow.contracts import RoleBindingProfileDocument, RoleBindingProfileInfo
from verdflow.contracts import strategy_version_accepts

from .database import LibraryDatabase
from .errors import (
    LibraryIntegrityError,
    RoleBindingProfileConflictError,
    RoleBindingProfileNotFoundError,
    StrategyVersionNotInstalledError,
)
from .identities import stable_id


class RoleBindingProfileRepository:
    def __init__(self, database: LibraryDatabase) -> None:
        self._database = database

    def save(
        self,
        document: RoleBindingProfileDocument,
        *,
        expected_generation: int,
        changed_at: str,
    ) -> RoleBindingProfileInfo:
        if expected_generation < 0:
            raise ValueError("expected_generation must not be negative")
        with self._database.transaction() as connection:
            strategies = connection.execute(
                "SELECT package_version FROM strategy_packages WHERE package_id = ?",
                (document.strategy_id,),
            ).fetchall()
            if not any(strategy_version_accepts(document.strategy_version, row["package_version"]) for row in strategies):
                raise StrategyVersionNotInstalledError(
                    f"{document.strategy_id}@{document.strategy_version}"
                )
            current = connection.execute(
                "SELECT * FROM role_binding_profiles WHERE profile_id = ?",
                (document.profile_id,),
            ).fetchone()
            if current is None:
                if expected_generation != 0:
                    raise RoleBindingProfileConflictError(
                        "Profile generation is stale"
                    )
                profile_entry_id = stable_id("profile", document.profile_id)
                generation = 1
                created_at = changed_at
            else:
                if int(current["generation"]) != expected_generation:
                    raise RoleBindingProfileConflictError(
                        "Profile generation is stale"
                    )
                _require_same_identity(current, document)
                if (
                    str(current["semantic_hash"]) == document.semantic_hash
                    and bytes(current["canonical_json"]) == document.canonical_json
                ):
                    return self.get(document.profile_id)
                profile_entry_id = str(current["profile_entry_id"])
                generation = expected_generation + 1
                created_at = str(current["created_at"])
            connection.execute(
                """
                INSERT INTO role_binding_profiles(
                    profile_entry_id, profile_id, display_name,
                    strategy_id, strategy_version, target_type, provider_id,
                    catalog_id, generation, semantic_hash, canonical_json,
                    byte_size, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(profile_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    strategy_version = excluded.strategy_version,
                    catalog_id = excluded.catalog_id,
                    generation = excluded.generation,
                    semantic_hash = excluded.semantic_hash,
                    canonical_json = excluded.canonical_json,
                    byte_size = excluded.byte_size,
                    updated_at = excluded.updated_at
                """,
                (
                    profile_entry_id,
                    document.profile_id,
                    document.display_name,
                    document.strategy_id,
                    document.strategy_version,
                    document.target_type,
                    document.provider_id,
                    document.catalog_id,
                    generation,
                    document.semantic_hash,
                    document.canonical_json,
                    len(document.canonical_json),
                    created_at,
                    changed_at,
                ),
            )
        return self.get(document.profile_id)

    def get(self, profile_id: str) -> RoleBindingProfileInfo:
        row = self._find_row(profile_id)
        if row is None:
            raise RoleBindingProfileNotFoundError(profile_id)
        return _to_info(row)

    def find(self, profile_id: str) -> RoleBindingProfileInfo | None:
        row = self._find_row(profile_id)
        return _to_info(row) if row is not None else None

    def list(
        self,
        *,
        target_type: str | None = None,
        catalog_id: str | None = None,
        strategy_id: str | None = None,
        strategy_version: str | None = None,
    ) -> tuple[RoleBindingProfileInfo, ...]:
        clauses: list[str] = []
        parameters: list[str] = []
        for clause, value in (
            ("target_type = ?", target_type),
            ("catalog_id = ?", catalog_id),
            ("strategy_id = ?", strategy_id),
            ("strategy_version = ?", strategy_version),
        ):
            if value is not None:
                clauses.append(clause)
                parameters.append(value)
        query = "SELECT * FROM role_binding_profiles"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY display_name, profile_id"
        rows = self._database.connection.execute(query, parameters).fetchall()
        return tuple(_to_info(row) for row in rows)

    def load_document(self, profile_id: str) -> RoleBindingProfileDocument:
        row = self._find_row(profile_id)
        if row is None:
            raise RoleBindingProfileNotFoundError(profile_id)
        try:
            return RoleBindingProfileDocument(
                profile_id=str(row["profile_id"]),
                display_name=str(row["display_name"]),
                strategy_id=str(row["strategy_id"]),
                strategy_version=str(row["strategy_version"]),
                target_type=str(row["target_type"]),
                provider_id=str(row["provider_id"]),
                catalog_id=(
                    str(row["catalog_id"])
                    if row["catalog_id"] is not None
                    else None
                ),
                semantic_hash=str(row["semantic_hash"]),
                canonical_json=bytes(row["canonical_json"]),
            )
        except ValueError as error:
            raise LibraryIntegrityError(
                "Stored Profile bytes failed hash validation"
            ) from error

    def delete(self, profile_id: str) -> bool:
        with self._database.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM role_binding_profiles WHERE profile_id = ?",
                (profile_id,),
            )
            return cursor.rowcount > 0

    def _find_row(self, profile_id: str) -> sqlite3.Row | None:
        return self._database.connection.execute(
            "SELECT * FROM role_binding_profiles WHERE profile_id = ?",
            (profile_id,),
        ).fetchone()


def _to_info(row: sqlite3.Row) -> RoleBindingProfileInfo:
    return RoleBindingProfileInfo(
        profile_entry_id=str(row["profile_entry_id"]),
        profile_id=str(row["profile_id"]),
        display_name=str(row["display_name"]),
        strategy_id=str(row["strategy_id"]),
        strategy_version=str(row["strategy_version"]),
        target_type=str(row["target_type"]),
        provider_id=str(row["provider_id"]),
        catalog_id=str(row["catalog_id"]) if row["catalog_id"] is not None else None,
        generation=int(row["generation"]),
        semantic_hash=str(row["semantic_hash"]),
        byte_size=int(row["byte_size"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _require_same_identity(
    row: sqlite3.Row,
    document: RoleBindingProfileDocument,
) -> None:
    existing = (
        str(row["strategy_id"]),
        str(row["target_type"]),
        str(row["provider_id"]),
    )
    requested = (
        document.strategy_id,
        document.target_type,
        document.provider_id,
    )
    if existing != requested or not strategy_version_accepts(
        str(row["strategy_version"]), document.strategy_version
    ):
        raise RoleBindingProfileConflictError(
            "Profile strategy/target cannot change or move to an incompatible version"
        )
