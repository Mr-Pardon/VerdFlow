"""Exact, disposable spatial lookup used only by the imagery detail subprocess."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import closing
import errno
import math
from pathlib import Path
import shutil
import sqlite3
from struct import Struct
from typing import Any, BinaryIO

from shapely import from_wkb, intersects
from shapely.geometry import Polygon
from shapely.strtree import STRtree


_FREE_DISK_RESERVE = 64 * 1024**2
_READ_BATCH_BYTES = 8 * 1024**2
_READ_BATCH_ROWS = 256
_POLYGON_HEADER = Struct("<BII")
_RING_SIZE = Struct("<I")
_XY = Struct("<dd")


def _polygon(row: dict) -> Polygon:
    rings = row["footprint_boundary_xyz"]
    return Polygon(rings[0], rings[1:])


class MemoryTileRegions:
    def __init__(self, rows: list[dict]) -> None:
        self._ids = [row["id"] for row in rows]
        self._tree = STRtree([_polygon(row) for row in rows])

    def matches(self, footprint: Polygon) -> Iterator[tuple[int, Polygon]]:
        for index in sorted(self._tree.query(footprint, predicate="intersects").tolist()):
            yield index + 1, self._tree.geometries[index]

    def ids(self, ordinals: Sequence[int]) -> list[str]:
        return [self._ids[index - 1] for index in ordinals]


class DiskTileRegions:
    @classmethod
    def build(
        cls, path: Path, geometry: Path, expected_count: int | None, progress: Callable[[], None],
    ) -> DiskTileRegions:
        with geometry.open("rb") as source:
            return cls.build_rows(path, _region_wkb_rows(source, progress), expected_count, progress)

    @classmethod
    def build_rows(
        cls, path: Path, rows: Iterator[tuple[str, bytes | bytearray, tuple[float, float, float, float]]],
        expected_count: int | None, progress: Callable[[], None], *, allow_empty: bool = False,
    ) -> DiskTileRegions:
        with closing(sqlite3.connect(path)) as db:
            db.executescript("""
                PRAGMA journal_mode=OFF;
                PRAGMA synchronous=OFF;
                PRAGMA cache_size=-4096;
                PRAGMA temp_store=FILE;
                CREATE TABLE region (ordinal INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL, wkb BLOB NOT NULL);
                CREATE VIRTUAL TABLE bounds USING rtree(ordinal, min_x, max_x, min_y, max_y);
            """)
            count = 0
            for count, (region_id, wkb, box) in enumerate(rows, 1):
                if count % 512 == 1:
                    progress()
                    if shutil.disk_usage(path.parent).free < _FREE_DISK_RESERVE:
                        raise OSError(errno.ENOSPC, "Imagery detail cache has insufficient free disk space")
                if not isinstance(region_id, str) or not region_id or (expected_count is not None and count > expected_count):
                    raise ValueError("Invalid imagery candidate row")
                x0, y0, x1, y1 = box
                db.execute("INSERT INTO region VALUES (?, ?, ?)", (count, region_id, wkb))
                db.execute("INSERT INTO bounds VALUES (?, ?, ?, ?, ?)", (count, x0, x1, y0, y1))
                del wkb
            if (count == 0 and not allow_empty) or (expected_count is not None and count != expected_count):
                raise ValueError("Imagery candidate count does not match its manifest")
            db.commit()
        return cls(path)

    def __init__(self, path: Path) -> None:
        self._db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        self._db.executescript("PRAGMA cache_size=-4096; PRAGMA temp_store=FILE;")

    def close(self) -> None:
        self._db.close()

    def matches(self, footprint: Polygon) -> Iterator[tuple[int, Polygon]]:
        x0, y0, x1, y1 = footprint.bounds
        # Sort only ordinals, not geometry blobs, for stable overlap order.
        with closing(self._db.execute("""
            SELECT bounds.ordinal, length(region.wkb) FROM bounds
            JOIN region ON region.ordinal = bounds.ordinal
            WHERE min_x <= ? AND max_x >= ? AND min_y <= ? AND max_y >= ?
            ORDER BY bounds.ordinal
        """, (x1, x0, y1, y0))) as matches:
            batch: list[int] = []
            byte_size = 0
            for ordinal, length in matches:
                if len(batch) == _READ_BATCH_ROWS or (batch and byte_size + length > _READ_BATCH_BYTES):
                    yield from self._matching_batch(footprint, batch)
                    batch, byte_size = [], 0
                batch.append(ordinal)
                byte_size += length
            if batch:
                yield from self._matching_batch(footprint, batch)

    def _matching_batch(self, footprint: Polygon, ordinals: Sequence[int]) -> Iterator[tuple[int, Polygon]]:
        parameters = ",".join("?" for _ in ordinals)
        records = self._db.execute(
            f"SELECT ordinal, wkb FROM region WHERE ordinal IN ({parameters}) ORDER BY ordinal", ordinals,
        ).fetchall()
        polygons = from_wkb([wkb for _, wkb in records])
        for (ordinal, _), polygon, match in zip(records, polygons, intersects(footprint, polygons), strict=True):
            if match:
                yield ordinal, polygon

    def ids(self, ordinals: Sequence[int]) -> list[str]:
        ids = []
        for start in range(0, len(ordinals), 512):
            batch = ordinals[start:start + 512]
            parameters = ",".join("?" for _ in batch)
            ids.extend(region_id for (region_id,) in self._db.execute(
                f"SELECT id FROM region WHERE ordinal IN ({parameters}) ORDER BY ordinal", batch,
            ))
        if len(ids) != len(ordinals):
            raise ValueError("Imagery detail index is incomplete")
        return ids


_Event = tuple[str, Any]


def _region_wkb_rows(
    source: BinaryIO, progress: Callable[[], None],
) -> Iterator[tuple[str, bytearray, tuple[float, float, float, float]]]:
    import ijson

    events = iter(ijson.basic_parse(source, use_float=True))
    if next(events, None) != ("start_map", None):
        raise ValueError("Imagery candidate must be an object")
    saw_rows = False
    while (field := next(events))[0] != "end_map":
        value = next(events)
        if field != ("map_key", "regions"):
            _skip_value(events, value)
            continue
        if saw_rows or value[0] != "start_array":
            raise ValueError("Imagery candidate must have one Region array")
        saw_rows = True
        while (item := next(events))[0] != "end_array":
            if item[0] != "start_map":
                raise ValueError("Invalid imagery candidate row")
            region_id, geometry = None, None
            while (member := next(events))[0] != "end_map":
                value = next(events)
                if member == ("map_key", "id"):
                    if region_id is not None or value[0] != "string":
                        raise ValueError("Invalid imagery candidate ID")
                    region_id = value[1]
                elif member == ("map_key", "footprint_boundary_xyz"):
                    if geometry is not None or value[0] != "start_array":
                        raise ValueError("Invalid imagery candidate boundary")
                    geometry = _polygon_wkb(events, progress)
                else:
                    _skip_value(events, value)
            if not region_id or geometry is None:
                raise ValueError("Imagery candidate row is incomplete")
            yield region_id, *geometry
            del geometry
    if next(events, None) is not None or not saw_rows:
        raise ValueError("Invalid imagery candidate document")


def _polygon_wkb(
    events: Iterator[_Event], progress: Callable[[], None],
) -> tuple[bytearray, tuple[float, float, float, float]]:
    # Display only needs XY. Keep numeric coordinates packed rather than
    # building a nested JSON list and then another complete native polygon.
    wkb = bytearray(_POLYGON_HEADER.pack(1, 3, 0))
    xmin = ymin = math.inf
    xmax = ymax = -math.inf
    rings = processed = 0
    while (ring := next(events))[0] != "end_array":
        if ring[0] != "start_array":
            raise ValueError("Invalid imagery candidate ring")
        rings += 1
        position = len(wkb)
        wkb.extend(b"\0" * _RING_SIZE.size)
        points = 0
        first = None
        while (point := next(events))[0] != "end_array":
            if point[0] != "start_array":
                raise ValueError("Invalid imagery candidate coordinate")
            x_event, x = next(events)
            y_event, y = next(events)
            tail = next(events)
            if tail[0] == "number":
                tail = next(events)
            if x_event != "number" or y_event != "number" or tail[0] != "end_array":
                raise ValueError("Invalid imagery candidate coordinate")
            if first is None:
                first = (x, y)
            wkb.extend(_XY.pack(x, y))
            points += 1
            if x < xmin:
                xmin = x
            if x > xmax:
                xmax = x
            if y < ymin:
                ymin = y
            if y > ymax:
                ymax = y
            processed += 1
            if processed % 32_768 == 0:
                progress()
        if first is not None and (x, y) != first:
            wkb.extend(_XY.pack(*first))
            points += 1
        if points < 4:
            raise ValueError("Invalid imagery candidate ring length")
        _RING_SIZE.pack_into(wkb, position, points)
    if not rings or not all(map(math.isfinite, (xmin, ymin, xmax, ymax))):
        raise ValueError("Invalid imagery candidate bounds")
    _POLYGON_HEADER.pack_into(wkb, 0, 1, 3, rings)
    return wkb, (xmin, ymin, xmax, ymax)


def _skip_value(events: Iterator[_Event], value: _Event) -> None:
    depth = int(value[0] in ("start_map", "start_array"))
    while depth:
        event, _ = next(events)
        if event in ("start_map", "start_array"):
            depth += 1
        elif event in ("end_map", "end_array"):
            depth -= 1
