"""Disposable exact-ring lookup for large RegionSet viewer Artifacts."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager, suppress
from dataclasses import dataclass
import errno
import json
from pathlib import Path
import shutil
import sqlite3
from typing import Any, BinaryIO

import ijson
from ijson.common import JSONError, ObjectBuilder


_FREE_DISK_RESERVE = 64 * 1024**2


class RegionIndexError(ValueError):
    """The exact geometry Artifact cannot be indexed safely."""


@dataclass(frozen=True, slots=True)
class DiskRegionReviewIndex:
    path: Path
    byte_size: int

    @classmethod
    def build(
        cls, path: Path, source: BinaryIO, *, count: int, cancelled: Callable[[], bool],
        on_boundary: Callable[[str, list[Any]], None] | None = None,
    ) -> DiskRegionReviewIndex:
        def records() -> Iterator[tuple[str, str]]:
            total = 0
            for region_id, rings in _boundaries(source, cancelled):
                if total % 1024 == 0 and shutil.disk_usage(path.parent).free < _FREE_DISK_RESERVE:
                    raise OSError(errno.ENOSPC, "Region review cache has insufficient free disk space")
                total += 1
                if total > count:
                    raise RegionIndexError("Region geometry count differs from its Core record")
                if on_boundary is not None:
                    on_boundary(region_id, rings)
                yield region_id, json.dumps(rings, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            if total != count:
                raise RegionIndexError("Region geometry count differs from its Core record")

        with closing(sqlite3.connect(path)) as db:
            db.set_progress_handler(cancelled, 32768)
            db.executescript("""
                PRAGMA journal_mode = OFF;
                PRAGMA synchronous = OFF;
                PRAGMA cache_size = -4096;
                CREATE TABLE boundaries (id TEXT PRIMARY KEY, rings TEXT NOT NULL) WITHOUT ROWID;
            """)
            db.executemany("INSERT INTO boundaries VALUES (?, ?)", records())
            db.commit()
        return cls(path, path.stat().st_size)

    @contextmanager
    def lookup(self) -> Iterator[Callable[[str], dict[str, object] | None]]:
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("PRAGMA cache_size = -4096")

            def get(region_id: str) -> dict[str, object] | None:
                row = db.execute("SELECT rings FROM boundaries WHERE id = ?", (region_id,)).fetchone()
                return {"footprint_boundary_xyz": json.loads(row[0])} if row is not None else None

            yield get

    def close(self) -> None:
        with suppress(OSError):
            self.path.unlink(missing_ok=True)


def _boundaries(
    source: BinaryIO, cancelled: Callable[[], bool],
) -> Iterator[tuple[str, list[Any]]]:
    events = iter(ijson.basic_parse(source, use_float=True))
    try:
        if next(events) != ("start_map", None):
            raise RegionIndexError("Region geometry must be an object")
        header: dict[str, object] = {}
        saw_regions = False
        total = 0
        while (field := next(events))[0] != "end_map":
            name = field[1]
            first = next(events)
            if name != "regions":
                keep = name in {"schema", "version", "region_count"}
                value = _value(events, first, keep, cancelled)
                if keep:
                    if name in header:
                        raise RegionIndexError("Region geometry contains a duplicate header field")
                    header[name] = value
                continue
            if saw_regions or first != ("start_array", None):
                raise RegionIndexError("Region geometry must contain one Region array")
            saw_regions = True
            while (row := next(events))[0] != "end_array":
                if cancelled():
                    raise InterruptedError("Region review closed while building its index")
                if row != ("start_map", None):
                    raise RegionIndexError("Region geometry row must be an object")
                members: dict[str, Any] = {}
                while (item := next(events))[0] != "end_map":
                    key = item[1]
                    keep = key in {"id", "footprint_boundary_xyz"}
                    value = _value(events, next(events), keep, cancelled)
                    if keep:
                        members[key] = value
                region_id = members.get("id")
                rings = members.get("footprint_boundary_xyz", [])
                if not isinstance(region_id, str) or not region_id or not isinstance(rings, list):
                    raise RegionIndexError("Region geometry row is invalid")
                total += 1
                yield region_id, rings
        if next(events, None) is not None:
            raise RegionIndexError("Region geometry contains trailing data")
        if not saw_regions or header.get("schema") != "verdflow.regions" or str(header.get("version")) != "1":
            raise RegionIndexError("Region geometry schema is unsupported")
        if header.get("region_count", total) != total:
            raise RegionIndexError("Region geometry count is inconsistent")
    except (JSONError, UnicodeError, StopIteration) as error:
        raise RegionIndexError("Region geometry is not valid UTF-8 JSON") from error


def _value(
    events: Iterator[tuple[str, Any]], first: tuple[str, Any], keep: bool, cancelled: Callable[[], bool],
) -> Any:
    event, value = first
    if event not in {"start_map", "start_array"}:
        return value if keep else None
    builder = ObjectBuilder() if keep else None
    if builder is not None:
        builder.event(event, value)
    depth = 1
    for index, (event, value) in enumerate(events, 1):
        if index % 32768 == 0 and cancelled():
            raise InterruptedError("Region review closed while reading a boundary")
        if event in {"start_map", "start_array"}:
            depth += 1
        elif event in {"end_map", "end_array"}:
            depth -= 1
        if builder is not None:
            builder.event(event, value)
        if not depth:
            return builder.value if builder is not None else None
    raise StopIteration
