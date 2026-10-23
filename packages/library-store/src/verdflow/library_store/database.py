"""SQLite lifecycle for the multi-reader user library."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Iterator


class LibraryDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._connection: sqlite3.Connection | None = None

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("Library database is closed")
        return self._connection

    def open(self) -> None:
        self._connection = sqlite3.connect(
            self.path,
            timeout=30,
            isolation_level=None,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA synchronous = FULL")
        self._connection.execute("PRAGMA busy_timeout = 30000")

    def close(self) -> None:
        if self._connection is None:
            return
        try:
            # Other short-lived LibraryStore instances may still be connected.
            self._connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
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
        return () if messages == ("ok",) else messages

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
