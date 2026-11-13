"""Polygonize bounded raster tiles and join four-connected regions in pixel space."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import closing
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
import sqlite3
from typing import TYPE_CHECKING

from .errors import GeospatialDataError

if TYPE_CHECKING:
    from rasterio.io import DatasetReader
    from shapely.geometry import Polygon


VECTOR_TILE = 512
INLINE_MERGE_BYTES = 16 * 1024 * 1024
MERGE_BATCH_BYTES = 4 * 1024 * 1024
MERGE_BATCH_PARTS = 512


@dataclass(slots=True)
class RegionTally:
    filtered: int = 0


def polygon_tiles(
    dataset: DatasetReader,
    scratch: Path,
    minimum: int,
    tally: RegionTally,
    progress: Callable[[str, int, int], None],
) -> Iterator[tuple[Polygon, int]]:
    from affine import Affine
    from rasterio.features import shapes
    from rasterio.windows import Window
    from shapely import from_wkb, union_all
    from shapely.geometry import shape

    width, height = dataset.width, dataset.height
    cols = (width + VECTOR_TILE - 1) // VECTOR_TILE
    total = cols * ((height + VECTOR_TILE - 1) // VECTOR_TILE)
    with closing(sqlite3.connect(scratch)) as db:
        # Scratch only: a cancelled attempt is discarded, never recovered.
        db.executescript("""
            PRAGMA journal_mode=OFF;
            PRAGMA cache_size=-8192;
            CREATE TABLE piece(id INTEGER PRIMARY KEY, parent INTEGER, size INTEGER, pixels INTEGER, wkb BLOB);
            CREATE TABLE edge(tile INTEGER, side INTEGER, pos INTEGER, id INTEGER,
                              PRIMARY KEY(tile, side, pos)) WITHOUT ROWID;
        """)
        progress("vectorizing", 0, total)
        for tile in range(total):
            x, y = (tile % cols) * VECTOR_TILE, (tile // cols) * VECTOR_TILE
            w, h = min(VECTOR_TILE, width - x), min(VECTOR_TILE, height - y)
            tile_mask = dataset.read(1, window=Window(x, y, w, h))
            incoming = {(side, pos): piece for side, pos, piece in db.execute(
                "SELECT side, pos, id FROM edge WHERE tile=?", (tile,),
            )}
            db.execute("DELETE FROM edge WHERE tile=?", (tile,))
            for geometry, _ in shapes(tile_mask, mask=tile_mask == 1, connectivity=4,
                                      transform=Affine.translation(x, y)):
                polygon = shape(geometry)
                pixels = round(polygon.area)
                left, top, right, bottom = _boundary_pixels(polygon, x, y, w, h, width, height)
                neighbors = {incoming[(side, pos)] for side, positions in enumerate((left, top))
                             for pos in positions if (side, pos) in incoming}
                if not (neighbors or right or bottom):
                    if pixels >= minimum:
                        yield polygon, pixels
                    else:
                        tally.filtered += 1
                    continue
                piece = db.execute(
                    "INSERT INTO piece(parent, size, pixels, wkb) VALUES (NULL, 1, ?, ?)",
                    (pixels, polygon.wkb),
                ).lastrowid
                for neighbor in sorted(neighbors):
                    _join(db, piece, neighbor)
                db.executemany("INSERT INTO edge VALUES (?,?,?,?)",
                               ((tile + 1, 0, pos, piece) for pos in right))
                db.executemany("INSERT INTO edge VALUES (?,?,?,?)",
                               ((tile + cols, 1, pos, piece) for pos in bottom))
            db.commit()
            progress("vectorizing", tile + 1, total)

        count = db.execute("SELECT count(*) FROM piece").fetchone()[0]
        progress("stitching", 0, count)
        for start in range(1, count + 1, 512):
            end = min(start + 512, count + 1)
            db.executemany("UPDATE piece SET parent=? WHERE id=?",
                           ((_root(db, piece), piece) for piece in range(start, end)))
            progress("stitching", end - 1, count)
        db.execute("CREATE INDEX piece_component ON piece(parent)")
        progress("merging", 0, count)
        completed = 0
        for root, pixels, byte_size, parts in db.execute(
            "SELECT parent, sum(pixels), sum(length(wkb)), count(*) FROM piece GROUP BY parent ORDER BY parent",
        ):
            if pixels < minimum:
                tally.filtered += 1
            else:
                with closing(db.execute("SELECT wkb FROM piece WHERE parent=? ORDER BY id", (root,))) as rows:
                    if byte_size <= INLINE_MERGE_BYTES:
                        pieces = [from_wkb(wkb) for (wkb,) in rows]
                        polygon = pieces[0] if len(pieces) == 1 else union_all(pieces).simplify(0, preserve_topology=True)
                        del pieces
                    else:
                        polygon = _merge_component_batched((wkb for (wkb,) in rows),
                            lambda processed: progress("merging", completed + processed, count))
                if polygon.geom_type != "Polygon" or not polygon.is_valid or polygon.area != pixels:
                    raise GeospatialDataError("imagery_invalid_geometry", "跨块区域未能保持有效边界，本次没有自动修补或填洞。")
                yield polygon, pixels
            completed += parts
            progress("merging", completed, count)


def _merge_component_batched(records: Iterator[bytes], progress: Callable[[int], None]) -> Polygon:
    from shapely import from_wkb, union, union_all

    batch = []
    weight = processed = 0
    levels = []

    def merge_batch() -> None:
        nonlocal weight, processed
        merged = batch[0] if len(batch) == 1 else union_all(batch)
        processed += len(batch)
        batch.clear()
        weight = 0
        # Merge balanced groups, not the whole prefix with each new tile.
        for level, previous in enumerate(levels):
            if previous is None:
                levels[level] = merged
                break
            levels[level] = None
            merged = union(previous, merged)
        else:
            levels.append(merged)
        progress(processed)

    for wkb in records:
        if batch and (weight + len(wkb) > MERGE_BATCH_BYTES or len(batch) >= MERGE_BATCH_PARTS):
            merge_batch()
        batch.append(from_wkb(wkb))
        weight += len(wkb)
    if batch:
        merge_batch()
    merged = None
    for previous in levels:
        if previous is not None:
            merged = previous if merged is None else union(previous, merged)
    assert merged is not None
    levels.clear()
    previous = None
    return merged.simplify(0, preserve_topology=True) if processed > 1 else merged


def _root(db: sqlite3.Connection, piece: int) -> int:
    trail = []
    while (parent := db.execute("SELECT parent FROM piece WHERE id=?", (piece,)).fetchone()[0]) not in (None, piece):
        trail.append(piece)
        piece = parent
    db.executemany("UPDATE piece SET parent=? WHERE id=?", ((piece, child) for child in trail))
    return piece


def _join(db: sqlite3.Connection, left: int, right: int) -> None:
    a, b = _root(db, left), _root(db, right)
    if a == b:
        return
    a_size = db.execute("SELECT size FROM piece WHERE id=?", (a,)).fetchone()[0]
    b_size = db.execute("SELECT size FROM piece WHERE id=?", (b,)).fetchone()[0]
    if a_size < b_size:
        a, b = b, a
    db.execute("UPDATE piece SET parent=? WHERE id=?", (a, b))
    db.execute("UPDATE piece SET size=? WHERE id=?", (a_size + b_size, a))


def _boundary_pixels(
    polygon: Polygon, x: int, y: int, w: int, h: int, width: int, height: int,
) -> tuple[set[int], set[int], set[int], set[int]]:
    left, top, right, bottom = set(), set(), set(), set()
    for ring in (polygon.exterior, *polygon.interiors):
        for (ax, ay), (bx, by) in pairwise(ring.coords):
            if ax == bx:
                side = left if ax == x and x else right if ax == x + w and x + w < width else None
                if side is not None:
                    side.update(range(int(min(ay, by)), int(max(ay, by))))
            if ay == by:
                side = top if ay == y and y else bottom if ay == y + h and y + h < height else None
                if side is not None:
                    side.update(range(int(min(ax, bx)), int(max(ax, bx))))
    return left, top, right, bottom
