"""Pure document conversion performed inside the geospatial subprocess."""

from __future__ import annotations

import json
import math
from pathlib import Path
import zipfile
from typing import Any, Iterable
from .errors import GeospatialDataError, invalid_polygon_message


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
        raise GeospatialDataError("geo_empty_vector", "区域数据中没有面要素，请检查是否选择了空图层；项目已保留。")

    raster_metadata = _raster_metadata(Path(raster["source_path"]))
    vector_bounds = _combined_bounds(regions)
    if not _bounds_intersect(vector_bounds, raster_metadata["bounds"]):
        raise GeospatialDataError("geo_bounds_mismatch", "SHP/GeoJSON 与 TIF 的范围不相交，无法采样高程。请在 GIS 中检查覆盖范围、米制坐标及二者的对齐关系；VerdFlow 不会自动重投影或移动数据。")
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
    with _open_raster(Path(raster["source_path"])) as dataset:
        if band > dataset.count:
            raise GeospatialDataError("geo_band_missing", f"TIF 不存在第 {band} 波段（文件仅有 {dataset.count} 个波段），请选择有效的高程波段。")
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
                raise GeospatialDataError("geo_point_outside_raster", f"第 {index + 1} 个种植点超出 TIF 覆盖范围。请提供覆盖全部种植区域且坐标已对齐的高程文件；本次未输出不完整高程结果。")
            sample = next(dataset.sample([(x, y)], indexes=band, masked=True))
            value = sample[0]
            if bool(getattr(value, "mask", False)):
                raise GeospatialDataError("geo_raster_nodata", f"第 {index + 1} 个种植点落在 TIF 的 NoData 空洞中。请补齐高程或调整种植区域；不会以零高程替代。")
            z = float(value)
            if not math.isfinite(z) or (
                nodata is not None and math.isclose(z, float(nodata), rel_tol=0.0, abs_tol=0.0)
            ):
                raise GeospatialDataError("geo_raster_nodata", f"第 {index + 1} 个种植点的高程为空或无效。请检查 TIF 的 NoData 和高程波段；不会以零高程替代。")
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
        raise GeospatialDataError("geo_source_pair_required", "此工作流需要一份 SHP/GeoJSON 面数据和一份已对齐的高程 TIF，请检查项目数据源。")
    for item in (*vectors, *rasters):
        matrix = item.get("source_to_scene_matrix_row_major")
        if not isinstance(matrix, list) or tuple(float(value) for value in matrix) != IDENTITY_MATRIX:
            raise GeospatialDataError("geo_alignment_required", "地理数据工作流不支持运行时移动或缩放数据，请先在 GIS 中统一坐标和单位，再使用已对齐的 SHP＋TIF。")
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
            raise GeospatialDataError("geo_polygon_required", f"GeoJSON 第 {index + 1} 条要素不是面，请选择 Polygon/MultiPolygon 图层。")
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
            raise GeospatialDataError("geo_invalid_archive", "SHP 压缩包包含不安全路径，未解压数据。请重新打包一套 SHP 及其配套文件。")
        archive.extractall(destination)
    shp_files = list(destination.rglob("*.shp"))
    if len(shp_files) != 1:
        raise GeospatialDataError("geo_single_dataset_required", "压缩包中应只有一套 SHP 数据，请分别导入多个图层。")
    shp_path = shp_files[0]
    for suffix in (".shx", ".dbf"):
        if not shp_path.with_suffix(suffix).is_file():
            raise GeospatialDataError("geo_companions_missing", f"SHP 缺少同名 {suffix} 配套文件，请补齐后重新导入。")
    reader = shapefile.Reader(str(shp_path))
    features = []
    try:
        for shape_record in reader.iterShapeRecords():
            geometry = shape(shape_record.shape.__geo_interface__)
            if geometry.geom_type not in {"Polygon", "MultiPolygon"}:
                raise GeospatialDataError("geo_polygon_required", f"SHP 第 {len(features) + 1} 条要素不是面，请选择 Polygon/MultiPolygon 图层。")
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
    if polygon.is_empty or not polygon.is_valid or polygon.area <= 0:
        raise GeospatialDataError("geo_invalid_geometry", invalid_polygon_message(polygon, feature_index + 1))
    for ring in (polygon.exterior, *polygon.interiors):
        if not all(
            math.isfinite(float(coordinate[0])) and math.isfinite(float(coordinate[1]))
            for coordinate in ring.coords
        ):
            raise GeospatialDataError("geo_invalid_coordinates", f"第 {feature_index + 1} 条面要素含有无效坐标，请在 GIS 中检查并修复；未自动丢弃顶点。")


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
    with _open_raster(path) as dataset:
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


def _open_raster(path: Path):
    import rasterio
    from rasterio.errors import RasterioIOError
    try:
        return rasterio.open(path)
    except RasterioIOError as error:
        raise GeospatialDataError("geo_raster_unreadable", "无法读取高程 TIF，请检查文件是否完整、格式是否受支持，以及当前用户是否有读取权限。") from error


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
