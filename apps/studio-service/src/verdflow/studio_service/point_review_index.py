"""Disposable on-disk spatial projection for large point reviews."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from contextlib import closing, suppress
from dataclasses import dataclass
import errno
import json
from pathlib import Path
import shutil
import sqlite3

from .models import Bounds3D, PointReviewItem, PointViewportChunk


_FREE_DISK_RESERVE = 64 * 1024**2
_RESPONSE_BYTES = 8 * 1024**2


@dataclass(frozen=True, slots=True)
class DiskPointReviewIndex:
    path: Path
    byte_size: int
    bounds: Bounds3D | None

    @classmethod
    def build(
        cls, path: Path, items: Iterable[PointReviewItem], *,
        bounds: Bounds3D | None, cancelled: Callable[[], bool],
    ) -> DiskPointReviewIndex:
        def records() -> Iterator[tuple[object, ...]]:
            for item in items:
                if item.ordinal % 16384 == 0:
                    if cancelled():
                        raise InterruptedError("Point review closed while building its index")
                    if shutil.disk_usage(path.parent).free < _FREE_DISK_RESERVE:
                        raise OSError(errno.ENOSPC, "Point review cache has insufficient free disk space")
                yield (
                    item.ordinal, item.point_id, item.region_id, item.role_key,
                    *item.position, item.asset_id, *item.target_scale_xyz, item.group_id,
                )

        with closing(sqlite3.connect(path)) as db:
            db.set_progress_handler(cancelled, 32768)
            db.executescript("""
                PRAGMA journal_mode = OFF;
                PRAGMA synchronous = OFF;
                PRAGMA cache_size = -4096;
                PRAGMA temp_store = FILE;
                CREATE TABLE points (
                    ordinal INTEGER PRIMARY KEY, point_id TEXT NOT NULL, region_id TEXT NOT NULL,
                    role_key TEXT NOT NULL, x REAL NOT NULL, y REAL NOT NULL, z REAL NOT NULL,
                    asset_id TEXT NOT NULL, sx REAL NOT NULL, sy REAL NOT NULL, sz REAL NOT NULL, group_id TEXT
                );
            """)
            db.executemany("INSERT INTO points VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", records())
            db.commit()
            db.executescript("""
                CREATE UNIQUE INDEX point_ids ON points(point_id);
                CREATE INDEX points_x ON points(x);
                CREATE INDEX points_y ON points(y);
            """)
        return cls(path, path.stat().st_size, bounds)

    def chunk(
        self, viewport: tuple[float, float, float, float], *,
        roles: tuple[str, ...], assets: tuple[str, ...], after_ordinal: int, limit: int,
    ) -> PointViewportChunk:
        min_x, min_y, max_x, max_y = viewport
        table = "points NOT INDEXED"
        if self.bounds is not None:
            x_ratio = _coverage(min_x, max_x, self.bounds.min_x, self.bounds.max_x)
            y_ratio = _coverage(min_y, max_y, self.bounds.min_y, self.bounds.max_y)
            if min(x_ratio, y_ratio) <= 0.25:
                table = "points INDEXED BY points_x" if x_ratio <= y_ratio else "points INDEXED BY points_y"
        predicates = ["ordinal > ?", "x BETWEEN ? AND ?", "y BETWEEN ? AND ?"]
        params: list[object] = [after_ordinal, min_x, max_x, min_y, max_y]
        for column, values in (("role_key", roles), ("asset_id", assets)):
            if values:
                predicates.append(f"{column} IN (SELECT value FROM json_each(?))")
                params.append(json.dumps(values))
        params.append(limit + 1)
        query = f"""
            SELECT ordinal, point_id, region_id, role_key, x, y, z, asset_id, sx, sy, sz, group_id
            FROM {table} WHERE {" AND ".join(predicates)} ORDER BY ordinal LIMIT ?
        """
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("PRAGMA cache_size = -4096")
            db.execute("PRAGMA temp_store = FILE")
            return point_review_page(
                (
                    PointReviewItem(point_id, ordinal, region_id, role, (x, y, z), asset, (sx, sy, sz), group)
                    for ordinal, point_id, region_id, role, x, y, z, asset, sx, sy, sz, group
                    in db.execute(query, params)
                ),
                limit=limit,
            )

    def close(self) -> None:
        with suppress(OSError):
            self.path.unlink(missing_ok=True)


def point_review_page(items: Iterable[PointReviewItem], *, limit: int) -> PointViewportChunk:
    page: list[PointReviewItem] = []
    remaining = _RESPONSE_BYTES
    truncated = False
    for item in items:
        # At most six UTF-8 bytes per escaped character; reserve field and numeric overhead.
        weight = 512 + 6 * sum(
            len(value) for value in (item.point_id, item.region_id, item.role_key, item.asset_id, item.group_id or "")
        )
        if len(page) == limit or weight > remaining:
            if not page:
                raise ValueError("Point review item exceeds the response size budget")
            truncated = True
            break
        remaining -= weight
        page.append(item)
    return PointViewportChunk(tuple(page), page[-1].ordinal if truncated else None, truncated)


def _coverage(low: float, high: float, bound_low: float, bound_high: float) -> float:
    span = bound_high - bound_low
    if not 0 < span < float("inf"):
        return 1.0
    return max(0.0, min(high, bound_high) - max(low, bound_low)) / span
