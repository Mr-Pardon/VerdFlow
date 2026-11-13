"""Sample exact polygon coverage on a bounded display raster."""

from __future__ import annotations

from math import ceil, floor
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from affine import Affine
    from numpy import ndarray
    from shapely.geometry import Polygon


COMPLEX_DISPLAY_VERTICES = 262_144


def paint_polygon_centers(ids: ndarray, transform: Affine, polygon: Polygon, ordinal: int, *, on_chunk=None) -> None:
    from numpy import arange, copyto, uint32
    from shapely import destroy_prepared, intersects_xy, is_prepared, prepare

    inverse = ~transform
    x0, y0, x1, y1 = polygon.bounds
    columns, rows = zip(*(inverse * point for point in ((x0, y0), (x0, y1), (x1, y0), (x1, y1))))
    height, width = ids.shape
    left, right = max(0, floor(min(columns))), min(width, ceil(max(columns)))
    top, bottom = max(0, floor(min(rows))), min(height, ceil(max(rows)))
    if left >= right or top >= bottom:
        return

    prepared = is_prepared(polygon)
    if not prepared:
        prepare(polygon)
    a, b, c, d, e, f = transform[:6]
    columns = arange(left, right, dtype="float64")[None, :] + 0.5
    try:
        for start in range(top, bottom, 32):
            end = min(start + 32, bottom)
            rows = arange(start, end, dtype="float64")[:, None] + 0.5
            mask = intersects_xy(polygon, a * columns + b * rows + c, d * columns + e * rows + f)
            copyto(ids[start:end, left:right], uint32(ordinal), where=mask)
            if on_chunk is not None:
                on_chunk()
    finally:
        if not prepared:
            destroy_prepared(polygon)
