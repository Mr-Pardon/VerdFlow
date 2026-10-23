"""Strategy installation metadata persistence."""

from __future__ import annotations

from pathlib import Path
import sqlite3

from verdflow.contracts import (
    LibraryStrategyInfo,
    require_package_hash,
    require_sha256_hash,
)

from .database import LibraryDatabase
from .errors import StrategyInstallConflictError


class StrategyRepository:
    def __init__(self, database: LibraryDatabase) -> None:
        self._database = database

    def register(
        self,
        *,
        package_id: str,
        package_version: str,
        package_hash: str,
        display_name: str,
        payload_hash: str,
        install_relpath: str,
        installed_at: str,
    ) -> LibraryStrategyInfo:
        normalized = {
            "package_id": _require_text(package_id, "package_id"),
            "package_version": _require_text(package_version, "package_version"),
            "display_name": _require_text(display_name, "display_name"),
            "installed_at": _require_text(installed_at, "installed_at"),
        }
        require_package_hash(package_hash)
        require_sha256_hash(payload_hash)
        relative = Path(install_relpath)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("install_relpath must remain inside the library")
        entry_id = f"strategy_{package_hash.removeprefix('sha256:')[:24]}"
        try:
            with self._database.transaction() as connection:
                existing = connection.execute(
                    "SELECT * FROM strategy_packages WHERE package_hash = ?",
                    (package_hash,),
                ).fetchone()
                if existing is None:
                    connection.execute(
                        """
                        INSERT INTO strategy_packages(
                            library_entry_id, package_id, package_version,
                            package_hash, display_name, payload_hash,
                            install_relpath, installed_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            entry_id,
                            normalized["package_id"],
                            normalized["package_version"],
                            package_hash,
                            normalized["display_name"],
                            payload_hash,
                            relative.as_posix(),
                            normalized["installed_at"],
                        ),
                    )
                else:
                    expected = (
                        normalized["package_id"],
                        normalized["package_version"],
                        normalized["display_name"],
                        payload_hash,
                        relative.as_posix(),
                    )
                    actual = (
                        existing["package_id"],
                        existing["package_version"],
                        existing["display_name"],
                        existing["payload_hash"],
                        existing["install_relpath"],
                    )
                    if actual != expected:
                        raise StrategyInstallConflictError(
                            "Strategy package hash is already registered with "
                            "different metadata or bytes"
                        )
        except sqlite3.IntegrityError as error:
            raise StrategyInstallConflictError(
                "Strategy package metadata conflicts with an installed package"
            ) from error
        return self.get(package_hash)

    def get(self, package_hash: str) -> LibraryStrategyInfo:
        require_package_hash(package_hash)
        row = self._database.connection.execute(
            "SELECT * FROM strategy_packages WHERE package_hash = ?",
            (package_hash,),
        ).fetchone()
        if row is None:
            raise KeyError(package_hash)
        return _to_info(row)

    def find(self, package_hash: str) -> LibraryStrategyInfo | None:
        try:
            return self.get(package_hash)
        except KeyError:
            return None

    def list(self) -> tuple[LibraryStrategyInfo, ...]:
        rows = self._database.connection.execute(
            """
            SELECT * FROM strategy_packages
            ORDER BY display_name COLLATE NOCASE, package_id, package_version
            """
        ).fetchall()
        return tuple(_to_info(row) for row in rows)

    def storage_facts(self, package_hash: str) -> tuple[str, str]:
        require_package_hash(package_hash)
        row = self._database.connection.execute(
            """
            SELECT install_relpath, payload_hash
            FROM strategy_packages WHERE package_hash = ?
            """,
            (package_hash,),
        ).fetchone()
        if row is None:
            raise KeyError(package_hash)
        return str(row["install_relpath"]), str(row["payload_hash"])

    def unregister(self, package_hash: str) -> LibraryStrategyInfo:
        strategy = self.get(package_hash)
        with self._database.transaction() as connection:
            connection.execute(
                "DELETE FROM strategy_trust_events WHERE package_hash = ?",
                (package_hash,),
            )
            deleted = connection.execute(
                "DELETE FROM strategy_packages WHERE package_hash = ?",
                (package_hash,),
            ).rowcount
            if deleted != 1:
                raise KeyError(package_hash)
        return strategy


def _to_info(row: sqlite3.Row) -> LibraryStrategyInfo:
    return LibraryStrategyInfo(
        library_entry_id=str(row["library_entry_id"]),
        package_id=str(row["package_id"]),
        package_version=str(row["package_version"]),
        package_hash=str(row["package_hash"]),
        display_name=str(row["display_name"]),
    )


def _require_text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value.strip()
