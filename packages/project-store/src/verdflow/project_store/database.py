"""SQLite connection and transaction lifecycle."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .storage import ProjectStorageProfile


class ProjectDatabase:
    def __init__(
        self,
        path: Path,
        *,
        storage_profile: ProjectStorageProfile = ProjectStorageProfile.DURABLE,
    ) -> None:
        if not isinstance(storage_profile, ProjectStorageProfile):
            raise TypeError("storage_profile must be a ProjectStorageProfile")
        self.path = path
        self.storage_profile = storage_profile
        self._connection: sqlite3.Connection | None = None

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("Project database is closed")
        return self._connection

    def open(self) -> None:
        self._connection = sqlite3.connect(
            self.path,
            timeout=30,
            isolation_level=None,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        if self.storage_profile is ProjectStorageProfile.DURABLE:
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA synchronous = FULL")
        else:
            self._connection.execute("PRAGMA journal_mode = MEMORY")
            self._connection.execute("PRAGMA synchronous = OFF")
            self._connection.execute("PRAGMA temp_store = MEMORY")

    def close(self) -> None:
        if self._connection is None:
            return
        try:
            if self.storage_profile is ProjectStorageProfile.DURABLE:
                self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            self._connection.close()
            self._connection = None

    def backup_to(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        backup = sqlite3.connect(destination)
        try:
            self.connection.backup(backup)
        finally:
            backup.close()

    def integrity_errors(self) -> tuple[str, ...]:
        rows = self.connection.execute("PRAGMA integrity_check").fetchall()
        messages = tuple(str(row[0]) for row in rows)
        if messages == ("ok",):
            return ()
        return messages

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
        except Exception:
            connection.execute("ROLLBACK")
            raise
        else:
            connection.execute("COMMIT")


class ReadOnlyProjectDatabase:
    """Read committed project state without participating in writer ownership."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._connection: sqlite3.Connection | None = None

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("Project query database is closed")
        return self._connection

    def open(self) -> None:
        uri = f"{self.path.resolve().as_uri()}?mode=ro"
        self._connection = sqlite3.connect(
            uri,
            uri=True,
            timeout=30,
            isolation_level=None,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA query_only = ON")

    def close(self) -> None:
        if self._connection is None:
            return
        self._connection.close()
        self._connection = None

    @contextmanager
    def snapshot(self) -> Iterator[sqlite3.Connection]:
        connection = self.connection
        connection.execute("BEGIN")
        try:
            yield connection
        except Exception:
            connection.execute("ROLLBACK")
            raise
        else:
            connection.execute("COMMIT")
