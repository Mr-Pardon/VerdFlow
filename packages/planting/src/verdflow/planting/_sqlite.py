"""Lifecycle of disposable Planting indexes."""

from collections.abc import Iterator
from contextlib import closing, contextmanager
import os
from pathlib import Path
import sqlite3


@contextmanager
def scratch_database(path: Path) -> Iterator[sqlite3.Connection]:
    filename = _database_path(path)
    with filename.open("xb"):
        pass
    try:
        with closing(sqlite3.connect(filename)) as index:
            yield index
    finally:
        filename.unlink(missing_ok=True)


def _database_path(path: Path) -> Path:
    if os.name != "nt":
        return path
    full = os.path.abspath(path)
    if full.startswith("\\\\?\\"):
        return Path(full)
    # SQLite's Win32 VFS otherwise reserves room for sidecars within MAX_PATH.
    if full.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + full[2:])
    return Path("\\\\?\\" + full)
