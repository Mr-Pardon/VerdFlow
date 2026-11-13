"""Bounded raster projection for dense Region Viewer viewports."""

from __future__ import annotations

from base64 import b64encode
from collections.abc import Callable
import json
from math import ceil, floor
import zlib

from verdflow.contracts import RegionSummary

from .models import Bounds3D, RegionOverview, RegionOverviewItem


_MAX_SIDE = 1024
_MAX_RESPONSE_BYTES = 8 * 1024**2
_BATCH_VERTICES = 60_000
_BATCH_REGIONS = 2_048
_Boundary = list[tuple[tuple[float, float, float], ...]]


class RegionOverviewBuilder:
    def __init__(self, bounds: Bounds3D, *, cancelled: Callable[[], bool]) -> None:
        import numpy as np
        from rasterio.transform import from_bounds

        span_x = bounds.max_x - bounds.min_x
        span_y = bounds.max_y - bounds.min_y
        cell = max(span_x, span_y) / _MAX_SIDE
        width = min(_MAX_SIDE, max(1, ceil(span_x / cell)))
        height = min(_MAX_SIDE, max(1, ceil(span_y / cell)))
        self._bounds = bounds
        self._transform = from_bounds(bounds.min_x, bounds.min_y, bounds.max_x, bounds.max_y, width, height)
        self._raster = np.zeros((height, width), dtype="uint32")
        self._cancelled = cancelled
        self._batch: list[tuple[dict[str, object], int]] = []
        self._vertices = 0

    def _flush(self) -> None:
        from rasterio.features import rasterize

        if self._cancelled():
            raise InterruptedError("Region overview closed during projection")
        if self._batch:
            rasterize(self._batch, out=self._raster, transform=self._transform, all_touched=True, skip_invalid=False)
            self._batch.clear()
            self._vertices = 0

    def add(self, index: int, rings: _Boundary) -> None:
        rings = [ring for ring in rings if len(ring) >= 3]
        if not rings:
            return
        count = sum(map(len, rings))
        if len(self._batch) >= _BATCH_REGIONS or (self._batch and self._vertices + count > _BATCH_VERTICES):
            self._flush()
        self._batch.append(({"type": "Polygon", "coordinates": rings}, index + 1))
        self._vertices += count

    def add_polygon(self, index: int, polygon) -> None:
        import numpy as np
        from shapely import intersects_xy

        self._flush()
        x0, y0, x1, y1 = polygon.bounds
        left, top = (~self._transform) * (x0, y1)
        right, bottom = (~self._transform) * (x1, y0)
        height, width = self._raster.shape
        left, right = max(0, floor(left)), min(width, ceil(right))
        top, bottom = max(0, floor(top)), min(height, ceil(bottom))
        if left >= right or top >= bottom:
            return
        xs = self._transform.c + (np.arange(left, right)[None, :] + 0.5) * self._transform.a
        for start in range(top, bottom, 64):
            if self._cancelled():
                raise InterruptedError("Region overview closed during projection")
            end = min(start + 64, bottom)
            ys = self._transform.f + (np.arange(start, end)[:, None] + 0.5) * self._transform.e
            np.copyto(self._raster[start:end, left:right], np.uint32(index + 1), where=intersects_xy(polygon, xs, ys))

    def finish(self, regions: tuple[RegionSummary, ...]) -> RegionOverview:
        import numpy as np

        self._flush()
        raster = self._raster
        # Only cells represented at this resolution need a palette entry.
        while True:
            if self._cancelled():
                raise InterruptedError("Region overview closed during encoding")
            codes = np.unique(raster)
            nonzero = codes[codes != 0]
            pixels = np.searchsorted(nonzero, raster).astype("<u4")
            pixels += 1
            pixels[raster == 0] = 0
            packed = b64encode(zlib.compress(pixels.tobytes(), level=1)).decode("ascii")
            palette: list[RegionOverviewItem] = []
            remaining = _MAX_RESPONSE_BYTES - len(packed) - 1024
            for code in nonzero:
                region = regions[int(code) - 1]
                entry = RegionOverviewItem(region.region_id, region.classification, region.footprint_area_xy)
                remaining -= 1 + len(json.dumps(entry, ensure_ascii=False, separators=(",", ":")).encode())
                if remaining < 0:
                    break
                palette.append(entry)
            if len(palette) == nonzero.size:
                return RegionOverview(self._bounds, raster.shape[1], raster.shape[0], packed, tuple(palette))
            if max(raster.shape) <= 1:
                raise ValueError("Region overview reference exceeds the display response budget")
            # Aggregate rather than dropping every other pixel; thin and small shapes still contribute.
            pad_y, pad_x = raster.shape[0] % 2, raster.shape[1] % 2
            raster = np.pad(raster, ((0, pad_y), (0, pad_x)))
            raster = raster.reshape(raster.shape[0] // 2, 2, raster.shape[1] // 2, 2).max(axis=(1, 3))
