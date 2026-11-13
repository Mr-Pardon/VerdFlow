"""Isolated display reader for a single recognized GeoTIFF and its candidate geometry."""

from __future__ import annotations

from base64 import b64encode
from concurrent.futures import CancelledError, ThreadPoolExecutor
from contextlib import ExitStack, closing, nullcontext
import errno
import json
import math
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic

from affine import Affine
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.features import rasterize, sieve
from rasterio.io import MemoryFile
from rasterio.windows import Window
from shapely import get_num_coordinates, prepare
from shapely.geometry import Polygon, box

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from verdflow.geospatial_worker.imagery_tile_index import DiskTileRegions, MemoryTileRegions
from verdflow.geospatial_worker.imagery_paint import COMPLEX_DISPLAY_VERTICES, paint_polygon_centers
from verdflow.geospatial_worker.imagery_regions import RegionTally, polygon_tiles
from verdflow.geospatial_worker.protocol import IMAGERY_TILE_RESPONSE_BYTES


TILE_SIZE = 512
_RASTER_BATCH_VERTICES = 80_000
_RASTER_BATCH_ROWS = 2_048
REFINEMENT_MAX_LEVEL = 2
_LOCAL_REFINEMENT_MINIMUM = 256
_REFINEMENT_CONTEXT = 12
_FREE_DISK_RESERVE = 64 * 1024**2


def _png(rgb: np.ndarray) -> str:
    with MemoryFile() as memory:
        with memory.open(driver="PNG", width=rgb.shape[2], height=rgb.shape[1], count=3, dtype="uint8") as output:
            output.write(rgb)
        return b64encode(memory.read()).decode("ascii")


def _polygons(geometry):
    if geometry.is_empty:
        return
    if geometry.geom_type == "Polygon":
        yield geometry
    elif geometry.geom_type in {"MultiPolygon", "GeometryCollection"}:
        for part in geometry.geoms:
            yield from _polygons(part)


def _box_average(values: np.ndarray, radius: int) -> np.ndarray:
    size = 2 * radius + 1
    horizontal = np.pad(values, ((0, 0), (radius, radius)), mode="edge")
    sums = np.pad(horizontal, ((0, 0), (1, 0))).cumsum(axis=1, dtype=np.int32)
    vertical = np.pad(sums[:, size:] - sums[:, :-size], ((radius, radius), (0, 0)), mode="edge")
    sums = np.pad(vertical, ((1, 0), (0, 0))).cumsum(axis=0, dtype=np.int32)
    return (sums[size:] - sums[:-size] + size * size // 2) // (size * size)


def refinement_pixels(rgb: np.ndarray, candidate: np.ndarray, mode: str, strength: int, minimum: int) -> np.ndarray:
    r, g, b = rgb.astype(np.float32)
    exg = (2 * g - r - b) / np.maximum(r + g + b, 1)
    if mode == "greenness":
        proposed = exg < (35 + 2 * (strength - 50)) / 1000
    else:
        high, low = rgb.max(axis=0), rgb.min(axis=0)
        light = (r + 2 * g + b) / 4
        local_light = _box_average(light.astype(np.uint8), 10)
        exposure = (exg < 0.035) & ((high - low) < 0.34 * high) & (r >= g - 5) & (
            (light >= 87) & ((light >= local_light + 9) | (light >= 140)))
        density = _box_average(exposure.astype(np.uint8) * 255, 2)
        proposed = exposure & (density >= 103)
    proposed &= candidate
    if minimum > 1 and proposed.any():
        proposed &= sieve(proposed.astype(np.uint8), minimum, connectivity=4) == 1
    return proposed


def _read_proposals(dataset, mask, bands, x, y, width, height, mode, strength, minimum=0):
    halo = minimum + _REFINEMENT_CONTEXT
    left, top = max(0, x - halo), max(0, y - halo)
    right, bottom = min(dataset.width, x + width + halo), min(dataset.height, y + height + halo)
    window = Window(left, top, right - left, bottom - top)
    candidate = mask.read(1, window=window) == 1
    if not candidate[y-top:y-top+height, x-left:x-left+width].any():
        return np.zeros((height, width), dtype=bool)
    rgb = dataset.read(bands, window=window, masked=True).filled(0).clip(0, 255).astype(np.uint8)
    return refinement_pixels(rgb, candidate, mode, strength, minimum)[y-top:y-top+height, x-left:x-left+width]


def _index_proposals(image, mask_path, bands, options, path: Path, cancelled: Event, *, progress=None) -> None:
    mode, strength, minimum = options
    proposals = path / "proposals.tif"
    checked_at = float("-inf")

    def check_cancelled(*_args):
        nonlocal checked_at
        if cancelled.is_set():
            raise CancelledError()
        now = monotonic()
        if now - checked_at >= 1:
            checked_at = now
            if shutil.disk_usage(path).free < _FREE_DISK_RESERVE:
                raise OSError(errno.ENOSPC, "Imagery refinement cache has insufficient free disk space")

    def advance(*args):
        check_cancelled()
        if progress is not None:
            progress(*args)

    # This thread owns its GDAL handles; the foreground reader keeps serving base tiles.
    with rasterio.Env(GDAL_CACHEMAX=32 * 1024**2, GDAL_NUM_THREADS="1", GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
                      GDAL_PAM_ENABLED="NO"), rasterio.open(image, driver="GTiff", sharing=False) as dataset, \
            rasterio.open(mask_path, driver="GTiff", sharing=False) as mask:
        with rasterio.open(proposals, "w", driver="GTiff", width=dataset.width, height=dataset.height, count=1,
                           dtype="uint8", transform=Affine.identity(), tiled=True, blockxsize=TILE_SIZE,
                           blockysize=TILE_SIZE, compress="deflate", BIGTIFF="IF_SAFER", SPARSE_OK=True) as output:
            for _, window in output.block_windows(1):
                x, y, w, h = map(int, window.flatten())
                proposed = _read_proposals(dataset, mask, bands, x, y, w, h, mode, strength)
                if proposed.any():
                    output.write(proposed.astype(np.uint8), 1, window=window)
                advance()
        with rasterio.open(proposals) as binary, closing(
            polygon_tiles(binary, path / "components.sqlite", minimum, RegionTally(), advance),
        ) as polygons:
            rows = ((str(i), polygon.wkb, polygon.bounds) for i, (polygon, _) in enumerate(polygons, 1))
            with closing(DiskTileRegions.build_rows(path / "overlay.sqlite", rows, None, advance, allow_empty=True)):
                pass
    proposals.unlink()
    (path / "components.sqlite").unlink()


class LargeRefinement:
    def __init__(self, cache_path: Path, image: str, mask: str, bands: tuple) -> None:
        self.cache_path = cache_path
        self.sources = image, mask, bands
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="imagery-refinement")
        self.options = self.work = self.future = self.cancelled = self.index = None

    def _clear_finished(self):
        if self.index is not None:
            self.index.close()
            self.index = None
        if self.work is not None:
            self.work.cleanup()
        self.work = self.future = self.cancelled = None

    def discard(self):
        self.options = None
        if self.cancelled is not None:
            self.cancelled.set()
        if self.future is not None and self.future.done():
            self._clear_finished()

    def close(self):
        self.discard()
        self.pool.shutdown(wait=True)
        self._clear_finished()

    def lookup(self, options):
        if options != self.options:
            self.discard()
            if self.future is not None:
                return None
            self.options = options
            self.work = TemporaryDirectory(prefix="refinement-", dir=self.cache_path, ignore_cleanup_errors=True)
            self.cancelled = Event()
            self.future = self.pool.submit(_index_proposals, *self.sources, options, Path(self.work.name), self.cancelled)
        if not self.future.done():
            return None
        self.future.result()
        if self.index is None:
            self.index = DiskTileRegions(Path(self.work.name) / "overlay.sqlite")
        return self.index


class ImageryTiles:
    def __init__(self, dataset, regions: DiskTileRegions | MemoryTileRegions, grid: dict, mask=None,
                 large_refinement: LargeRefinement | None = None) -> None:
        self.dataset = dataset
        self.regions = regions
        self.bands = tuple(grid["bands"])
        self.mask = mask
        self.large_refinement = large_refinement
        if (dataset.driver != "GTiff" or (dataset.width, dataset.height) != (grid["width"], grid["height"])
                or tuple(dataset.transform[:6]) != tuple(grid["transform"]) or len(self.bands) != 3
                or any(not 1 <= band <= dataset.count for band in self.bands)):
            raise ValueError("Unexpected imagery grid")
        block_rows, block_columns = (max(values) for values in zip(*dataset.block_shapes, strict=True))
        sizes = [np.dtype(dtype).itemsize for dtype in dataset.dtypes]
        if block_rows * block_columns * (sum(sizes) if dataset.profile.get("interleave") == "pixel" else max(sizes)) > 128 * 1024**2:
            raise ValueError("Unsafe imagery decode block")

    def read(self, level: int, column: int, row: int, refinement: tuple[str, int, int] | None = None) -> dict | None:
        if any(type(value) is not int or value < 0 for value in (level, column, row)) or level > 16:
            raise ValueError("Invalid tile coordinates")
        step = 1 << level
        span = TILE_SIZE * step
        x, y = column * span, row * span
        dataset = self.dataset
        w, h = min(span, dataset.width - x), min(span, dataset.height - y)
        if min(w, h) <= 0:
            raise ValueError("Tile outside imagery")
        options = refinement if self.mask is not None and level <= REFINEMENT_MAX_LEVEL else None
        area_index = None
        refinement_failed = False
        if options is not None:
            mode, strength, minimum = options
            if (mode not in {"greenness", "patches"} or type(strength) is not int or type(minimum) is not int
                    or not 0 <= strength <= 100 or minimum < 0):
                raise ValueError("Invalid imagery refinement options")
            if mode == "patches":
                options = (mode, 0, minimum)
        if (self.large_refinement is not None and options is not None
                and _LOCAL_REFINEMENT_MINIMUM < options[2] <= dataset.width * dataset.height):
            try:
                area_index = self.large_refinement.lookup(options)
                if area_index is None:
                    return {"retry_after_ms": 350, "refinement_pending": True}
            except Exception:
                refinement_failed = True
        elif self.large_refinement is not None and (refinement is None or level <= REFINEMENT_MAX_LEVEL):
            self.large_refinement.discard()
        width, height = math.ceil(w / step), math.ceil(h / step)
        window = Window(x, y, w, h)
        overview = max((factor for factor in dataset.overviews(self.bands[0]) if factor <= step), default=1)
        if w * h > 16_777_216 * overview**2:
            return None  # Leave the existing overview visible for expensive coarse reads.
        transform = dataset.window_transform(window) * Affine.scale(w / width, h / height)
        rgb = dataset.read(self.bands, window=window, out_shape=(3, height, width),
            resampling=Resampling.bilinear, masked=True).filled(0).clip(0, 255).astype("uint8")

        # A one-pixel gutter gives neighboring tiles the same boundary treatment at the seam.
        gutter = transform * Affine.translation(-1, -1)
        shape = (height + 2, width + 2)
        footprint = Polygon([gutter * point for point in ((0, 0), (shape[1], 0), shape[::-1], (0, shape[0]))])
        prepare(footprint)
        ids = np.zeros(shape, dtype="uint32")
        batch = []
        vertices = 0

        def flush() -> None:
            nonlocal vertices
            if batch:
                rasterize(batch, out=ids, transform=gutter)
                batch.clear()
                vertices = 0

        with closing(self.regions.matches(footprint)) as matches:
            for ordinal, geometry in matches:
                if get_num_coordinates(geometry) > COMPLEX_DISPLAY_VERTICES:
                    flush()
                    paint_polygon_centers(ids, gutter, geometry, ordinal)
                    continue
                for part in _polygons(geometry if footprint.covers(geometry) else geometry.intersection(footprint)):
                    weight = int(get_num_coordinates(part))
                    if len(batch) >= _RASTER_BATCH_ROWS or (batch and vertices + weight > _RASTER_BATCH_VERTICES):
                        flush()
                    batch.append((part, ordinal))
                    vertices += weight
        flush()
        represented = np.unique(ids)
        represented = represented[represented != 0]
        regions = self.regions.ids(represented.tolist())
        if represented.size:
            slots = np.searchsorted(represented, ids).astype("uint32") + 1
            slots[ids == 0] = 0
            ids = slots
        result = {"width": width, "height": height, "transform": list(transform[:6]), "region_ids": regions,
                "base_png": _png(rgb), "ids_png": _png(np.stack([(ids >> shift).astype("uint8") for shift in (0, 8, 16)]))}
        if options is not None:
            if refinement_failed:
                result["refinement_unavailable"] = True
            else:
                result["refinement_png"] = self._refinement(x, y, w, h, width, height, options, area_index)
        return result

    def _refinement(self, x: int, y: int, w: int, h: int, width: int, height: int, options, area_index) -> str:
        mode, strength, minimum = options
        if minimum > self.dataset.width * self.dataset.height:
            pixels = np.zeros((height, width), dtype=np.uint8)
        elif area_index is not None:
            raster = np.zeros((height, width), dtype=np.uint32)
            # A center exactly on an integer edge samples the right/bottom source pixel.
            transform = Affine.translation(x + 1e-7, y + 1e-7) * Affine.scale(w / width, h / height)
            with closing(area_index.matches(box(x, y, x + w, y + h))) as matches:
                for _, polygon in matches:
                    paint_polygon_centers(raster, transform, polygon, 1)
            pixels = raster.astype(np.uint8)
        else:
            if minimum > _LOCAL_REFINEMENT_MINIMUM:
                raise ValueError("Large refinement requires a cache workspace")
            proposed = _read_proposals(self.dataset, self.mask, self.bands, x, y, w, h, mode, strength, minimum)
            rows = ((np.arange(height) + 0.5) * h / height).astype(int)
            columns = ((np.arange(width) + 0.5) * w / width).astype(int)
            pixels = proposed[np.ix_(rows, columns)].astype(np.uint8)
        empty = np.zeros_like(pixels)
        return _png(np.stack((pixels, empty, empty)))


def main() -> None:
    image_path, geometry_path, grid_payload, cache_path = sys.argv[1:]
    grid = json.loads(grid_payload)
    with rasterio.Env(GDAL_CACHEMAX=32 * 1024**2, GDAL_NUM_THREADS="1", GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_PAM_ENABLED="NO"), \
            rasterio.open(image_path, driver="GTiff", sharing=False) as dataset:
        if cache_path:
            regions = DiskTileRegions.build(
                Path(cache_path), Path(geometry_path), grid.get("expected_region_count"),
                lambda: print("progress", flush=True),
            )
            scope = closing(regions)
        else:
            with open(geometry_path, "rb") as stream:
                rows = json.load(stream)["regions"]
            expected = grid.get("expected_region_count")
            if expected is not None and len(rows) != expected:
                raise ValueError("Imagery candidate count does not match its manifest")
            regions = MemoryTileRegions(rows)
            del rows
            scope = nullcontext(regions)
        with scope, ExitStack() as resources:
            mask = None
            if mask_path := grid.get("refinement_mask"):
                try:
                    candidate = resources.enter_context(rasterio.open(mask_path, driver="GTiff", sharing=False))
                    if (candidate.shape == dataset.shape and candidate.transform == dataset.transform
                            and candidate.crs == dataset.crs and candidate.count == 1):
                        mask = candidate
                except rasterio.errors.RasterioIOError:
                    pass  # The original review remains available without the optional mask.
            large = None
            if mask is not None and grid.get("refinement_cache"):
                large = LargeRefinement(Path(grid["refinement_cache"]), image_path, mask_path, tuple(grid["bands"]))
                resources.callback(large.close)
            tiles = ImageryTiles(dataset, regions, grid, mask, large)
            print("ready", flush=True)
            for line in sys.stdin.buffer:
                try:
                    result = tiles.read(*json.loads(line))
                except Exception:
                    result = None
                payload = json.dumps(result, separators=(",", ":"))
                print(payload if len(payload) < IMAGERY_TILE_RESPONSE_BYTES else "null", flush=True)


if __name__ == "__main__":
    main()
