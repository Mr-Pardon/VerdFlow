"""Write planar polygon datasets without projecting or repairing coordinates."""

from dataclasses import dataclass
import io
import math
from typing import Sequence
import zipfile


@dataclass(frozen=True, slots=True)
class ShapefilePolygon:
    polygon_id: str
    name: str
    coordinates: tuple[tuple[float, float, float], ...]
    holes: tuple[tuple[tuple[float, float, float], ...], ...] = ()


def export_polygon_shapefile(polygons: Sequence[ShapefilePolygon]) -> bytes:
    """Return a UTF-8 Polygon Shapefile ZIP in the caller's original XY frame."""
    import shapefile

    if not polygons:
        raise ValueError("At least one polygon is required")
    streams = {suffix: io.BytesIO() for suffix in ("shp", "shx", "dbf")}
    with shapefile.Writer(**streams, shapeType=shapefile.POLYGON, encoding="utf-8") as writer:
        writer.field("REGION_ID", "C", size=80)
        writer.field("NAME", "C", size=254)
        writer.field("POINTS", "N", size=10)
        writer.field("AREA_2D", "F", size=24, decimal=6)
        writer.field("Z_MIN", "F", size=24, decimal=6)
        writer.field("Z_MAX", "F", size=24, decimal=6)
        seen: set[str] = set()
        for polygon in polygons:
            if not polygon.polygon_id or polygon.polygon_id in seen:
                raise ValueError("Polygon IDs must be nonempty and unique")
            seen.add(polygon.polygon_id)
            if len(polygon.polygon_id.encode("utf-8")) > 80 or len(polygon.name.encode("utf-8")) > 254:
                raise ValueError("Polygon labels exceed the Shapefile field size")
            points = list(polygon.coordinates)
            if polygon.holes:
                from shapely.geometry import Polygon
                from .errors import GeospatialDataError, invalid_polygon_message
                geometry = Polygon([p[:2] for p in points], [[p[:2] for p in ring] for ring in polygon.holes])
                if geometry.is_empty or not geometry.is_valid:
                    raise GeospatialDataError("shp_invalid_geometry", invalid_polygon_message(geometry, len(seen)))
            if any(len(p) != 3 or not all(math.isfinite(v) for v in p) for p in points):
                raise ValueError("Polygon coordinates must be finite XYZ values")
            if len(points) > 1 and points[0][:2] == points[-1][:2]:
                points.pop()
            if len({p[:2] for p in points}) < 3:
                raise ValueError("Polygon requires three distinct XY coordinates")
            x0, y0, _ = points[0]
            twice_area = sum(
                (a[0] - x0) * (b[1] - y0) - (b[0] - x0) * (a[1] - y0)
                for a, b in zip(points, points[1:] + points[:1])
            )
            if not math.isfinite(twice_area) or twice_area == 0:
                raise ValueError("Polygon area must be positive and finite")
            # Shapefile exterior rings use clockwise winding.
            ring = [[p[0], p[1]] for p in points]
            if twice_area > 0:
                ring.reverse()
            rings = [ring + [ring[0]]]
            for hole in polygon.holes:
                if len(hole) < 3 or any(len(p) != 3 or not all(math.isfinite(v) for v in p) for p in hole):
                    raise ValueError("Polygon holes require at least three finite XYZ points")
                inner = [[p[0], p[1]] for p in hole]
                if inner[0] == inner[-1]:
                    inner.pop()
                hx, hy = inner[0]
                signed = sum((a[0] - hx) * (b[1] - hy) - (b[0] - hx) * (a[1] - hy)
                             for a, b in zip(inner, inner[1:] + inner[:1]))
                if signed == 0 or len(set(map(tuple, inner))) < 3:
                    raise ValueError("Polygon hole area must be positive")
                if signed < 0:
                    inner.reverse()
                rings.append(inner + [inner[0]])
            writer.poly(rings)
            area = float(geometry.area) if polygon.holes else abs(twice_area) / 2
            writer.record(polygon.polygon_id, polygon.name, len(points) + sum(map(len, polygon.holes)), area,
                          min(p[2] for p in points), max(p[2] for p in points))
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for suffix, stream in streams.items():
            archive.writestr(f"regions.{suffix}", stream.getvalue())
        archive.writestr("regions.cpg", "UTF-8")
    return output.getvalue()
