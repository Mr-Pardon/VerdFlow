"""Pure document conversion performed inside the geospatial subprocess."""

from __future__ import annotations

import json
import math
from pathlib import Path
import zipfile
from typing import Any, Iterable


IDENTITY_MATRIX = (
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
)


def prepare_regions(command: dict[str, Any], workdir: Path) -> dict[str, Any]:
    vector, raster = _source_pair(command)
    features, vector_metadata = _read_vector(vector, workdir)
    regions: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []
    for feature_index, (geometry, properties) in enumerate(features):
        polygons = list(geometry.geoms) if geometry.geom_type == "MultiPolygon" else [geometry]
        for part_index, polygon in enumerate(polygons):
            _validate_polygon(polygon, feature_index, part_index)
            region_id = f"region_{feature_index + 1:04d}_{part_index + 1:03d}"
            min_x, min_y, max_x, max_y = (float(value) for value in polygon.bounds)
            row = {
                "id": region_id,
                "source_member_id": vector["source_member_id"],
                "label": _property_text(
                    properties,
                    ("label", "name", "Name", "NAME"),
                    default=f"Region {feature_index + 1}.{part_index + 1}",
                ),
                "tags": _tags_text(properties.get("tags")),
                "bbox": [min_x, min_y, 0.0, max_x, max_y, 0.0],
                "footprint_boundary_xyz": [
                    _ring_xyz(polygon.exterior.coords),
                    *(_ring_xyz(ring.coords) for ring in polygon.interiors),
                ],
            }
            classification = _property_text(
                properties,
                ("classification", "greenbelt_type"),
                default="",
            )
            strategy_hint = _property_text(
                properties,
                ("strategy_hint",),
                default="",
            )
            if classification:
                row["classification"] = classification
            if strategy_hint:
                row["strategy_hint"] = strategy_hint
            regions.append(row)
            metrics.append(
                {
                    "id": region_id,
                    "source_member_id": vector["source_member_id"],
                    "footprint_area_xy": float(polygon.area),
                    "boundary_perimeter_xy": float(polygon.length),
                    "hole_count": len(polygon.interiors),
                }
            )
    if not regions:
        raise ValueError("Vector source contains no Polygon geometry")

    raster_metadata = _raster_metadata(Path(raster["source_path"]))
    vector_bounds = _combined_bounds(regions)
    if not _bounds_intersect(vector_bounds, raster_metadata["bounds"]):
        raise ValueError("Vector and GeoTIFF bounds do not intersect")
    source_id = _required_text(command, "source_id")
    return {
        "source_manifest": {
            "schema": "verdflow.region-source-manifest",
            "version": "1",
            "source_id": source_id,
            "alignment": "user_supplied",
            "crs_validation": "not_performed",
            "reprojection": "not_performed",
            "coordinate_frame": {
                "name": "User-aligned geospatial source",
                "handedness": "right",
                "up_axis": "+Z",
                "linear_unit": "meter",
                "crs_kind": "unknown",
                "crs_definition": None,
                "local_origin": [0.0, 0.0, 0.0],
                "offset_applied": False,
            },
            "vector": {
                **vector_metadata,
                "source_member_id": vector["source_member_id"],
                "bounds": vector_bounds,
                "region_count": len(regions),
            },
            "raster": {
                **raster_metadata,
                "source_member_id": raster["source_member_id"],
            },
        },
        "regions": {
            "schema": "verdflow.regions",
            "version": "1",
            "coordinate_space": "source_scene_world",
            "offset_applied": False,
            "region_count": len(regions),
            "regions": regions,
        },
        "geometry_metrics": {
            "schema": "verdflow.region-metrics",
            "version": "1",
            "coordinate_space": "source_scene_world",
            "offset_applied": False,
            "region_count": len(metrics),
            "regions": metrics,
        },
    }


def sample_elevation(command: dict[str, Any]) -> dict[str, Any]:
    _, raster = _source_pair(command)
    points_path = Path(_required_text(command, "points_path"))
    payload = json.loads(points_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != "verdflow.points":
        raise ValueError("Elevation input must be a verdflow.points document")
    if str(payload.get("version")) != "2" or payload.get("kind") != "generated":
        raise ValueError("Elevation input must be generated PointSet v2")
    rows = payload.get("points")
    if not isinstance(rows, list):
        raise ValueError("Elevation input points are required")
    band = _band(command.get("options"))
    try:
        import rasterio
    except ImportError as error:
        raise RuntimeError("rasterio is required by geospatial-worker") from error
    with rasterio.open(Path(raster["source_path"])) as dataset:
        if band > dataset.count:
            raise ValueError(f"GeoTIFF does not contain band {band}")
        nodata = dataset.nodatavals[band - 1]
        for index, point in enumerate(rows):
            if not isinstance(point, dict):
                raise ValueError(f"points[{index}] must be an object")
            generated = point.get("generated_position")
            if (
                not isinstance(generated, list)
                or len(generated) != 3
                or not all(_finite_number(value) for value in generated)
            ):
                raise ValueError(f"points[{index}].generated_position is invalid")
            x, y = float(generated[0]), float(generated[1])
            if not (
                dataset.bounds.left <= x <= dataset.bounds.right
                and dataset.bounds.bottom <= y <= dataset.bounds.top
            ):
                raise ValueError(f"Elevation point is outside GeoTIFF: {index}")
            sample = next(dataset.sample([(x, y)], indexes=band, masked=True))
            value = sample[0]
            if bool(getattr(value, "mask", False)):
                raise ValueError(f"Elevation point is NoData: {index}")
            z = float(value)
            if not math.isfinite(z) or (
                nodata is not None and math.isclose(z, float(nodata), rel_tol=0.0, abs_tol=0.0)
            ):
                raise ValueError(f"Elevation point is NoData: {index}")
            resolved = [x, y, z]
            point["terrain_sample"] = {
                "hit": True,
                "position": resolved,
                "normal": [0.0, 0.0, 1.0],
                "target": f"geotiff:band-{band}",
                "source_member_id": raster["source_member_id"],
            }
            point["resolved_position"] = resolved
    payload["kind"] = "elevation_resolved"
    payload["resolution_policy"] = "sample_hit_else_generated"
    return payload


def _source_pair(
    command: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    sources = command.get("sources")
    if not isinstance(sources, list):
        raise ValueError("Prepared geospatial command sources are required")
    vectors = [item for item in sources if isinstance(item, dict) and item.get("kind") in {"shapefile", "geojson"}]
    rasters = [item for item in sources if isinstance(item, dict) and item.get("kind") == "geotiff"]
    if len(vectors) != 1 or len(rasters) != 1 or len(sources) != 2:
        raise ValueError("Geospatial input requires one vector source and one GeoTIFF")
    for item in (*vectors, *rasters):
        matrix = item.get("source_to_scene_matrix_row_major")
        if not isinstance(matrix, list) or tuple(float(value) for value in matrix) != IDENTITY_MATRIX:
            raise ValueError("Geospatial source members must use identity transforms")
        _required_text(item, "source_member_id")
        _required_text(item, "source_path")
    return vectors[0], rasters[0]


def _read_vector(
    source: dict[str, Any],
    workdir: Path,
) -> tuple[list[tuple[Any, dict[str, Any]]], dict[str, Any]]:
    if source["kind"] == "geojson":
        return _read_geojson(Path(source["source_path"]))
    return _read_shapefile(Path(source["source_path"]), workdir)


def _read_geojson(
    path: Path,
) -> tuple[list[tuple[Any, dict[str, Any]]], dict[str, Any]]:
    try:
        from shapely.geometry import shape
    except ImportError as error:
        raise RuntimeError("shapely is required by geospatial-worker") from error
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("GeoJSON root must be an object")
    if payload.get("type") == "FeatureCollection":
        raw_features = payload.get("features")
    elif payload.get("type") == "Feature":
        raw_features = [payload]
    else:
        raw_features = [{"type": "Feature", "geometry": payload, "properties": {}}]
    if not isinstance(raw_features, list):
        raise ValueError("GeoJSON features must be an array")
    features: list[tuple[Any, dict[str, Any]]] = []
    for index, feature in enumerate(raw_features):
        if not isinstance(feature, dict) or not isinstance(feature.get("geometry"), dict):
            raise ValueError(f"GeoJSON feature {index} lacks geometry")
        geometry = shape(feature["geometry"])
        if geometry.geom_type not in {"Polygon", "MultiPolygon"}:
            raise ValueError(f"GeoJSON feature {index} is not Polygon/MultiPolygon")
        properties = feature.get("properties") or {}
        if not isinstance(properties, dict):
            raise ValueError(f"GeoJSON feature {index} properties must be an object")
        features.append((geometry, properties))
    return features, {
        "format": "geojson",
        "feature_count": len(features),
        "observed_crs": payload.get("crs"),
    }


def _read_shapefile(
    archive_path: Path,
    workdir: Path,
) -> tuple[list[tuple[Any, dict[str, Any]]], dict[str, Any]]:
    try:
        import shapefile
        from shapely.geometry import shape
    except ImportError as error:
        raise RuntimeError("pyshp and shapely are required by geospatial-worker") from error
    destination = workdir / "shapefile"
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        if any(Path(name).is_absolute() or ".." in Path(name).parts for name in names):
            raise ValueError("Shapefile archive contains an unsafe path")
        archive.extractall(destination)
    shp_files = list(destination.rglob("*.shp"))
    if len(shp_files) != 1:
        raise ValueError("Shapefile archive must contain exactly one .shp")
    shp_path = shp_files[0]
    for suffix in (".shx", ".dbf"):
        if not shp_path.with_suffix(suffix).is_file():
            raise ValueError(f"Shapefile archive is missing {suffix}")
    reader = shapefile.Reader(str(shp_path))
    features = []
    try:
        for shape_record in reader.iterShapeRecords():
            geometry = shape(shape_record.shape.__geo_interface__)
            if geometry.geom_type not in {"Polygon", "MultiPolygon"}:
                raise ValueError("Shapefile contains non-polygon geometry")
            features.append((geometry, dict(shape_record.record.as_dict())))
    finally:
        reader.close()
    prj = shp_path.with_suffix(".prj")
    return features, {
        "format": "shapefile",
        "feature_count": len(features),
        "observed_crs": (
            prj.read_text(encoding="utf-8", errors="replace") if prj.is_file() else None
        ),
    }


def _validate_polygon(polygon: Any, feature_index: int, part_index: int) -> None:
    label = f"feature {feature_index}, part {part_index}"
    if polygon.is_empty or not polygon.is_valid or polygon.area <= 0:
        raise ValueError(f"Polygon is invalid or empty: {label}")
    for ring in (polygon.exterior, *polygon.interiors):
        if not all(
            math.isfinite(float(coordinate[0])) and math.isfinite(float(coordinate[1]))
            for coordinate in ring.coords
        ):
            raise ValueError(f"Polygon contains non-finite coordinates: {label}")


def _ring_xyz(coordinates: Iterable[Any]) -> list[list[float]]:
    return [
        [float(coordinate[0]), float(coordinate[1]), 0.0]
        for coordinate in coordinates
    ]


def _raster_metadata(path: Path) -> dict[str, Any]:
    try:
        import rasterio
    except ImportError as error:
        raise RuntimeError("rasterio is required by geospatial-worker") from error
    with rasterio.open(path) as dataset:
        return {
            "format": "geotiff",
            "width": dataset.width,
            "height": dataset.height,
            "band_count": dataset.count,
            "bounds": [
                float(dataset.bounds.left),
                float(dataset.bounds.bottom),
                float(dataset.bounds.right),
                float(dataset.bounds.top),
            ],
            "transform": [float(value) for value in dataset.transform[:6]],
            "nodata": dataset.nodata,
            "observed_crs": str(dataset.crs) if dataset.crs is not None else None,
        }


def _combined_bounds(regions: list[dict[str, Any]]) -> list[float]:
    return [
        min(float(row["bbox"][0]) for row in regions),
        min(float(row["bbox"][1]) for row in regions),
        max(float(row["bbox"][3]) for row in regions),
        max(float(row["bbox"][4]) for row in regions),
    ]


def _bounds_intersect(left: list[float], right: list[float]) -> bool:
    return not (
        left[2] < right[0]
        or left[0] > right[2]
        or left[3] < right[1]
        or left[1] > right[3]
    )


def _property_text(
    properties: dict[str, Any],
    names: tuple[str, ...],
    *,
    default: str,
) -> str:
    for name in names:
        value = properties.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def _tags_text(value: object) -> str:
    if isinstance(value, list):
        return ",".join(str(item).strip() for item in value if str(item).strip())
    return str(value).strip() if value is not None else ""


def _band(options: object) -> int:
    if options is None:
        return 1
    if not isinstance(options, dict):
        raise ValueError("options must be an object")
    value = options.get("band", 1)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError("options.band must be a positive integer")
    return value


def _finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _required_text(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value.strip()
