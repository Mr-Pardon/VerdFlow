"""Write exact imagery candidate and review artifacts without holding the complete result."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager, nullcontext
from itertools import chain, islice
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from typing import Any, TextIO, TYPE_CHECKING

from .errors import GeospatialDataError
from .imagery import MAX_PREVIEW_VERTICES, _candidate_metadata, _candidate_row, _imagery_preview_row, _simplified_preview_rings
from .imagery_paint import COMPLEX_DISPLAY_VERTICES, paint_polygon_centers
from .processing import _ring_xyz
from .protocol import (
    IMAGERY_REVIEW_INDEX_ENCODING, IMAGERY_REVIEW_INDEX_INLINE_ROWS, IMAGERY_REVIEW_INDEX_ROW,
    IMAGERY_REVIEW_ORDINAL_LIMIT,
)

if TYPE_CHECKING:
    from shapely.geometry import Polygon


_INLINE_VERTICES = 120_000
_INLINE_REGIONS = 4_096
_OVERVIEW_BATCH_VERTICES = 80_000
_OVERVIEW_BATCH_ROWS = 2_048
_STREAM_REGION_VERTICES = COMPLEX_DISPLAY_VERTICES
_RING_WRITE_CHUNK = 4_096

_ProjectedRegion = tuple[tuple[float, float, int], "Polygon", int]


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


@contextmanager
def _ordered_regions(
    records: Iterator[_ProjectedRegion], path: Path,
) -> Iterator[tuple[int, Iterator[_ProjectedRegion]]]:
    from shapely import from_wkb, get_num_coordinates

    buffered: list[_ProjectedRegion] = []
    vertices = 0
    for record in records:
        buffered.append(record)
        vertices += int(get_num_coordinates(record[1]))
        if len(buffered) > _INLINE_REGIONS or vertices > _INLINE_VERTICES:
            break
    else:
        buffered.sort(key=lambda item: item[0])
        yield len(buffered), iter(buffered)
        return

    def stored(record: _ProjectedRegion) -> tuple[float, float, int, bytes, int]:
        key, polygon, pixels = record
        return (*key, polygon.wkb, pixels)

    with closing(sqlite3.connect(path)) as db:
        db.executescript("""
            PRAGMA journal_mode=OFF;
            PRAGMA cache_size=-4096;
            PRAGMA temp_store=FILE;
            CREATE TABLE candidates (
                x REAL, y REAL, ordinal INTEGER, wkb BLOB, pixels INTEGER,
                PRIMARY KEY(x, y, ordinal)
            ) WITHOUT ROWID;
        """)
        insert = "INSERT INTO candidates VALUES (?,?,?,?,?)"
        db.executemany(insert, map(stored, buffered))
        count = len(buffered)
        buffered.clear()

        def remaining() -> Iterator[tuple[float, float, int, bytes, int]]:
            nonlocal count
            for record in records:
                count += 1
                yield stored(record)

        db.executemany(insert, remaining())
        db.commit()
        with closing(db.execute("SELECT x, y, ordinal, wkb, pixels FROM candidates ORDER BY x, y, ordinal")) as rows:
            yield count, (((x, y, ordinal), from_wkb(wkb), pixels) for x, y, ordinal, wkb, pixels in rows)


class ImageryOverview:
    def __init__(self, preview: dict[str, Any]) -> None:
        from affine import Affine
        from numpy import zeros

        self._transform = Affine(*preview["transform"])
        self._ids = zeros((preview["height"], preview["width"]), dtype="uint32")
        self._batch: list[tuple[dict[str, Any], int]] = []
        self._vertices = 0

    def add(self, ordinal: int, rings: list, count: int) -> None:
        if len(self._batch) >= _OVERVIEW_BATCH_ROWS or (
            self._batch and self._vertices + count > _OVERVIEW_BATCH_VERTICES
        ):
            self._flush()
        self._batch.append(({"type": "Polygon", "coordinates": rings}, ordinal))
        self._vertices += count

    def add_large(self, ordinal: int, polygon: Polygon) -> None:
        self._flush()
        paint_polygon_centers(self._ids, self._transform, polygon, ordinal)

    def _flush(self) -> None:
        from rasterio.features import rasterize

        if self._batch:
            rasterize(self._batch, out=self._ids, transform=self._transform, skip_invalid=False)
            self._batch.clear()
            self._vertices = 0

    def write(self, path: Path) -> None:
        import rasterio

        self._flush()
        height, width = self._ids.shape
        with rasterio.open(path, "w", driver="PNG", width=width, height=height, count=3, dtype="uint8") as output:
            for band in range(3):
                output.write(((self._ids >> (band * 8)) & 255).astype("uint8"), band + 1)


def _write_large_region(stream: TextIO, row: dict[str, Any], polygon: Polygon, *, on_chunk=None) -> None:
    stream.write("{")
    keys = sorted((set(row) - {"pixel_count"}) | {"footprint_boundary_xyz"})
    for index, key in enumerate(keys):
        if index:
            stream.write(", ")
        stream.write(_json(key) + ": ")
        if key != "footprint_boundary_xyz":
            stream.write(_json(row[key]))
            continue
        stream.write("[")
        rings: list[list[list[float]]] = []
        vertices = 0
        ring_separator = ""

        def flush_rings() -> None:
            nonlocal vertices, ring_separator
            if rings:
                stream.write(ring_separator + _json(rings)[1:-1])
                rings.clear()
                vertices = 0
                ring_separator = ", "
                if on_chunk is not None:
                    on_chunk()

        for ring in chain((polygon.exterior,), polygon.interiors):
            coords = ring.coords
            count = len(coords)
            if vertices + count > _RING_WRITE_CHUNK:
                flush_rings()
            if count <= _RING_WRITE_CHUNK:
                rings.append(_ring_xyz(coords))
                vertices += count
                continue
            stream.write(ring_separator + "[")
            ring_separator = ", "
            coordinates = iter(coords)
            point_separator = ""
            while points := _ring_xyz(islice(coordinates, _RING_WRITE_CHUNK)):
                stream.write(point_separator + _json(points)[1:-1])
                point_separator = ", "
                if on_chunk is not None:
                    on_chunk()
            stream.write("]")
        flush_rings()
        stream.write("]")
    stream.write("}")


def write_imagery_candidate(
    output_dir: Path,
    records: Iterator[_ProjectedRegion],
    member_id: str,
    preview: dict[str, Any],
    progress: Callable[[str, int, int], None],
) -> dict[str, int | str]:
    """Private attempt outputs become visible to the supervisor only after success."""
    with TemporaryDirectory(dir=output_dir, prefix="candidate-") as scratch:
        stage = Path(scratch)
        spool = stage / "order.sqlite"
        with _ordered_regions(records, spool) as (count, ordered):
            if count >= IMAGERY_REVIEW_ORDINAL_LIMIT:
                raise GeospatialDataError(
                    "imagery_review_encoding_limit", "候选区域编号超过影像预览可表达的范围，请分区识别。",
                )
            pages = _write_sorted_candidate(stage, ordered, count, member_id, preview, progress)
        spool.unlink(missing_ok=True)
        for file in stage.iterdir():
            os.replace(file, output_dir / file.name)
    review: dict[str, int | str] = {"page_count": pages, "region_count": count}
    if count > IMAGERY_REVIEW_INDEX_INLINE_ROWS:
        review["index_encoding"] = IMAGERY_REVIEW_INDEX_ENCODING
    return review


def _write_sorted_candidate(
    stage: Path, records: Iterator[_ProjectedRegion], count: int, member_id: str,
    preview: dict[str, Any], progress: Callable[[str, int, int], None],
) -> int:
    from shapely import get_num_coordinates

    overview = ImageryOverview(preview)
    page: list[dict[str, Any]] = []
    page_index, page_vertices = 1, 0

    def flush_page() -> None:
        nonlocal page_index, page_vertices
        (stage / f"imagery_page_{page_index}.json").write_text(_json({"regions": page}), encoding="utf-8")
        page.clear()
        page_index += 1
        page_vertices = 0

    progress("writing_result", 0, count + 1)
    with (
        (stage / "regions.json").open("w", encoding="utf-8") as geometry,
        (stage / "region-metrics.json").open("w", encoding="utf-8") as metrics,
        (stage / "imagery-review-index.json").open("w", encoding="utf-8") as index_file,
        ((stage / "imagery-review-index.bin").open("wb")
         if count > IMAGERY_REVIEW_INDEX_INLINE_ROWS else nullcontext()) as packed_index,
    ):
        prefix = _json({"coordinate_space": "source_scene_world", "offset_applied": False, "region_count": count})
        for stream in (geometry, metrics):
            stream.write(prefix[:-1] + ', "regions": [')
        index_file.write('{"items": [')
        separator = ""
        for index, (_, polygon, pixels) in enumerate(records, 1):
            large = get_num_coordinates(polygon) > _STREAM_REGION_VERTICES
            if large:
                row, metric = _candidate_metadata(index, polygon, pixels, member_id)
                overview.add_large(index, polygon)
                outline = _simplified_preview_rings(polygon)
                display = {**row, "footprint_boundary_xyz": outline, "preview_mode": "simplified" if outline else "list_only"}
            else:
                row, metric = _candidate_row(index, polygon, pixels, member_id)
                rings = row["footprint_boundary_xyz"]
                overview.add(index, rings, sum(map(len, rings)))
                display = _imagery_preview_row(row)
            vertices = sum(map(len, display["footprint_boundary_xyz"]))
            if page and (len(page) >= 256 or page_vertices + vertices > MAX_PREVIEW_VERTICES):
                flush_page()
            page.append(display)
            page_vertices += vertices
            index_file.write(separator + _json({
                "region_id": row["id"], "label": row["label"], "pixel_count": pixels, "page": page_index,
            }))
            if packed_index is not None:
                packed_index.write(IMAGERY_REVIEW_INDEX_ROW.pack(pixels, page_index))
            if large:
                geometry.write(separator)
                _write_large_region(geometry, row, polygon)
            else:
                geometry.write(separator + _json({key: value for key, value in row.items() if key != "pixel_count"}))
            metrics.write(separator + _json(metric))
            separator = ", "
            if index % 128 == 0 or index == count:
                progress("writing_result", index, count + 1)
        for stream, schema in ((geometry, "verdflow.regions"), (metrics, "verdflow.region-metrics")):
            stream.write('], ' + _json({"schema": schema, "version": "1"})[1:])
        index_file.write("]}")
    if page:
        flush_page()
    overview.write(stage / "imagery-overview.png")
    progress("writing_result", count + 1, count + 1)
    return page_index - 1
