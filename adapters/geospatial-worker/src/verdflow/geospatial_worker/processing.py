"""Pure document conversion performed inside the geospatial subprocess."""

from __future__ import annotations

from contextlib import closing
from functools import lru_cache
import json
import math
from pathlib import Path
import shutil
import zipfile
from typing import Any, BinaryIO, Callable, Iterable, Iterator, TextIO
from .errors import GeospatialDataError, invalid_polygon_message
from .vector_json import read_geojson_features


IDENTITY_MATRIX = (
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
)
ELEVATION_POINT_CHUNK = 4096
ELEVATION_BATCH_WEIGHT = 48 * 1024 * 1024
ELEVATION_WINDOW_SIZE = 128
ELEVATION_CACHED_WINDOWS = 16


def prepare_regions(
    command: dict[str, Any], workdir: Path,
    report: Callable[[str, int, int], None] = lambda *_: None,
) -> dict[str, Any]:
    regions: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []

    def append(region: dict[str, Any], metric: dict[str, Any]) -> None:
        regions.append(region)
        metrics.append(metric)

    documents = _prepare_region_rows(command, workdir, report, append)
    documents["regions"]["regions"] = regions
    documents["geometry_metrics"]["regions"] = metrics
    return documents


def write_prepared_regions(
    command: dict[str, Any], workdir: Path, geometry: TextIO, metrics: TextIO,
    report: Callable[[str, int, int], None],
) -> dict[str, Any]:
    """Write exact v1 rows once; append document counts after reading the source."""
    geometry.write('{"regions": [')
    metrics.write('{"regions": [')
    separator = ""

    def write(region: dict[str, Any], metric: dict[str, Any]) -> None:
        nonlocal separator
        for stream, row in ((geometry, region), (metrics, metric)):
            stream.write(separator + json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False))
        separator = ", "

    documents = _prepare_region_rows(command, workdir, report, write)
    for stream, key in ((geometry, "regions"), (metrics, "geometry_metrics")):
        stream.write("], " + json.dumps(documents[key], ensure_ascii=False, sort_keys=True, allow_nan=False)[1:])
    return documents["source_manifest"]


def _prepare_region_rows(
    command: dict[str, Any], workdir: Path,
    report: Callable[[str, int, int], None],
    emit: Callable[[dict[str, Any], dict[str, Any]], None],
) -> dict[str, Any]:
    vector, raster = _source_pair(command)
    features, vector_metadata = _read_vector(vector, workdir, report)
    feature_count = region_count = 0
    vector_bounds = [math.inf, math.inf, -math.inf, -math.inf]
    with closing(features):
        for feature_index, (geometry, properties) in enumerate(features):
            feature_count += 1
            polygons = geometry.geoms if geometry.geom_type == "MultiPolygon" else (geometry,)
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
                emit(
                    row,
                    {
                        "id": region_id,
                        "source_member_id": vector["source_member_id"],
                        "footprint_area_xy": float(polygon.area),
                        "boundary_perimeter_xy": float(polygon.length),
                        "hole_count": len(polygon.interiors),
                    },
                )
                region_count += 1
                vector_bounds[0] = min(vector_bounds[0], min_x)
                vector_bounds[1] = min(vector_bounds[1], min_y)
                vector_bounds[2] = max(vector_bounds[2], max_x)
                vector_bounds[3] = max(vector_bounds[3], max_y)
    if not region_count:
        raise GeospatialDataError("geo_empty_vector", "区域数据中没有面要素，请检查是否选择了空图层；项目已保留。")

    raster_metadata = _raster_metadata(Path(raster["source_path"]))
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
                "feature_count": feature_count,
                "source_member_id": vector["source_member_id"],
                "bounds": vector_bounds,
                "region_count": region_count,
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
            "region_count": region_count,
        },
        "geometry_metrics": {
            "schema": "verdflow.region-metrics",
            "version": "1",
            "coordinate_space": "source_scene_world",
            "offset_applied": False,
            "region_count": region_count,
        },
    }


def sample_elevation(
    command: dict[str, Any],
    report: Callable[[str, int, int], None] = lambda *_: None,
) -> dict[str, Any]:
    """In-memory compatibility entry for small direct callers."""
    raster = _sampling_raster(command)
    points_path = Path(_required_text(command, "points_path"))
    payload = json.loads(points_path.read_text(encoding="utf-8"))
    _check_elevation_input(payload)
    rows = payload.get("points")
    if not isinstance(rows, list):
        raise ValueError("Elevation input points are required")
    batches = (rows[start:start + ELEVATION_POINT_CHUNK] for start in range(0, len(rows), ELEVATION_POINT_CHUNK))
    for _ in _sample_elevation_batches(batches, raster, _band(command.get("options")), len(rows), report):
        pass
    payload["kind"] = "elevation_resolved"
    payload["resolution_policy"] = "sample_hit_else_generated"
    return payload


def write_sampled_elevation(
    command: dict[str, Any],
    output: BinaryIO,
    report: Callable[[str, int, int], None],
) -> None:
    """Sample bounded point batches and retain the v2 document's existing bytes."""
    try:
        from verdflow.planting import PointSetReader, validate_point_header
    except ImportError as error:
        raise GeospatialDataError(
            "geo_point_runtime_missing",
            "高程工作进程缺少点位读取依赖。请使用随应用提供的运行环境，或在自定义环境中安装 VerdFlow 地理适配器及其依赖。",
        ) from error

    point_count = command.get("point_count")
    if type(point_count) is not int or point_count < 0:
        raise ValueError("Elevation input point_count must match the Core record")
    raster = _sampling_raster(command)
    band = _band(command.get("options"))
    leading = {
        "coordinate_space": "source_scene_world",
        "kind": "elevation_resolved",
        "offset_applied": False,
        "point_count": point_count,
    }
    output.write(_elevation_json(leading)[:-1] + b', "points": [')
    first = True
    with Path(_required_text(command, "points_path")).open("rb") as source:
        points = PointSetReader(source, expected_point_count=point_count)
        batches = points.batches(max_rows=ELEVATION_POINT_CHUNK, max_weight=ELEVATION_BATCH_WEIGHT)
        for batch in _sample_elevation_batches(batches, raster, band, point_count, report):
            if not first:
                output.write(b", ")
            output.write(_elevation_json(batch)[1:-1])
            first = False
        header = points.header
    _check_elevation_input(header)
    validate_point_header(header)
    if header["resolution_policy"] != "generated_position":
        raise ValueError("Elevation input must use generated positions")
    trailing = {
        "resolution_policy": "sample_hit_else_generated",
        "schema": header["schema"],
        "strategy": header["strategy"],
        "version": header["version"],
    }
    output.write(b"], " + _elevation_json(trailing)[1:])


def _elevation_json(value: object) -> bytes:
    # Keep legacy spacing and float representation as part of the artifact bytes.
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")


def _sampling_raster(command: dict[str, Any]) -> dict[str, Any]:
    if str(command.get("version")) == "2":
        sources = command.get("sources")
        if not isinstance(sources, list) or len(sources) != 1 or sources[0].get("kind") != "geotiff":
            raise ValueError("Explicit elevation sampling requires one bound GeoTIFF")
        return sources[0]
    return _source_pair(command)[1]


def _check_elevation_input(payload: object) -> None:
    if not isinstance(payload, dict) or payload.get("schema") != "verdflow.points":
        raise ValueError("Elevation input must be a verdflow.points document")
    if str(payload.get("version")) != "2" or payload.get("kind") != "generated":
        raise ValueError("Elevation input must be generated PointSet v2")


def _sample_elevation_batches(
    batches: Iterable[list[dict[str, Any]]],
    raster: dict[str, Any],
    band: int,
    point_count: int,
    report: Callable[[str, int, int], None],
) -> Iterator[list[dict[str, Any]]]:
    try:
        import numpy as np
        from rasterio.transform import rowcol
        from rasterio.windows import Window
    except ImportError as error:
        raise RuntimeError("rasterio is required by geospatial-worker") from error
    with _open_raster(Path(raster["source_path"])) as dataset:
        if band > dataset.count:
            raise GeospatialDataError("geo_band_missing", f"TIF 不存在第 {band} 波段（文件仅有 {dataset.count} 个波段），请选择有效的高程波段。")
        nodata = dataset.nodatavals[band - 1]
        bounds = dataset.bounds
        size = ELEVATION_WINDOW_SIZE
        columns = (dataset.width + size - 1) // size
        target = f"geotiff:band-{band}"
        source_member_id = raster["source_member_id"]

        @lru_cache(maxsize=ELEVATION_CACHED_WINDOWS)
        def read_window(tile: int):
            row, col = divmod(tile, columns)
            row, col = row * size, col * size
            window = dataset.read(
                band, masked=True, window=Window(
                    col, row, min(size, dataset.width - col), min(size, dataset.height - row),
                ),
            )
            return window.data, window.mask

        start = 0
        for batch in batches:
            if not batch:
                continue
            positions = [_generated_xy(point, start + i) for i, point in enumerate(batch)]
            xs, ys = np.asarray(positions).T
            rows, cols = rowcol(dataset.transform, xs, ys)
            outside = np.flatnonzero(
                (xs < bounds.left) | (xs > bounds.right) | (ys < bounds.bottom) | (ys > bounds.top)
                | ~((rows >= 0) & (rows < dataset.height) & (cols >= 0) & (cols < dataset.width))
            )
            # Only sample the valid prefix so failures still refer to the earliest point.
            count = int(outside[0]) if outside.size else len(batch)
            rows = rows[:count].astype(np.int64)
            cols = cols[:count].astype(np.int64)
            tiles = rows // size * columns + cols // size
            order = np.argsort(tiles, kind="stable")
            boundaries = np.flatnonzero(np.diff(tiles[order])) + 1
            heights = np.empty(count, dtype=np.float64)
            masked = np.zeros(count, dtype=bool)
            if count:
                for indices in np.split(order, boundaries):
                    data, mask = read_window(int(tiles[indices[0]]))
                    local_rows, local_cols = rows[indices] % size, cols[indices] % size
                    heights[indices] = data[local_rows, local_cols]
                    if np.ndim(mask):
                        masked[indices] = mask[local_rows, local_cols]
                    elif mask:
                        masked[indices] = True
            invalid = ~np.isfinite(heights)
            if nodata is not None:
                invalid |= heights == nodata
            missing = np.flatnonzero(masked | invalid)
            if missing.size:
                first = int(missing[0])
                index = start + first + 1
                if masked[first]:
                    raise GeospatialDataError("geo_raster_nodata", f"第 {index} 个种植点落在 TIF 的 NoData 空洞中。请补齐高程或调整种植区域；不会以零高程替代。")
                raise GeospatialDataError("geo_raster_nodata", f"第 {index} 个种植点的高程为空或无效。请检查 TIF 的 NoData 和高程波段；不会以零高程替代。")
            if outside.size:
                index = start + count + 1
                raise GeospatialDataError("geo_point_outside_raster", f"第 {index} 个种植点超出 TIF 覆盖范围。请提供覆盖全部种植区域且坐标已对齐的高程文件；本次未输出不完整高程结果。")

            for point, (x, y), z in zip(batch, positions, heights.tolist(), strict=True):
                resolved = [x, y, z]
                point["terrain_sample"] = {
                    "hit": True,
                    "position": resolved,
                    "normal": [0.0, 0.0, 1.0],
                    "target": target,
                    "source_member_id": source_member_id,
                }
                point["resolved_position"] = resolved
            start += len(batch)
            yield batch
            report("sampling_elevation", start, point_count)


def _generated_xy(point: Any, index: int) -> tuple[float, float]:
    if not isinstance(point, dict):
        raise ValueError(f"points[{index}] must be an object")
    generated = point.get("generated_position")
    if isinstance(generated, list) and len(generated) == 3:
        x, y, z = generated
        if (type(x) in (int, float) and type(y) in (int, float) and type(z) in (int, float)
                and math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
            return float(x), float(y)
    raise ValueError(f"points[{index}].generated_position is invalid")


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
    report: Callable[[str, int, int], None],
) -> tuple[Iterator[tuple[Any, dict[str, Any]]], dict[str, Any]]:
    if source["kind"] == "geojson":
        return _read_geojson(Path(source["source_path"]), report)
    return _read_shapefile(Path(source["source_path"]), workdir, report)


def _read_geojson(
    path: Path,
    report: Callable[[str, int, int], None],
) -> tuple[Iterator[tuple[Any, dict[str, Any]]], dict[str, Any]]:
    try:
        from shapely.geometry import shape
    except ImportError as error:
        raise RuntimeError("shapely is required by geospatial-worker") from error
    metadata: dict[str, Any] = {"format": "geojson"}

    def features():
        for index, feature in enumerate(read_geojson_features(path, metadata, report)):
            if not isinstance(feature, dict) or not isinstance(feature.get("geometry"), dict):
                raise ValueError(f"GeoJSON feature {index} lacks geometry")
            geometry = shape(feature["geometry"])
            if geometry.geom_type not in {"Polygon", "MultiPolygon"}:
                raise GeospatialDataError("geo_polygon_required", f"GeoJSON 第 {index + 1} 条要素不是面，请选择 Polygon/MultiPolygon 图层。")
            properties = feature.get("properties") or {}
            if not isinstance(properties, dict):
                raise ValueError(f"GeoJSON feature {index} properties must be an object")
            yield geometry, properties

    return features(), metadata


def _read_shapefile(
    archive_path: Path,
    workdir: Path,
    report: Callable[[str, int, int], None],
) -> tuple[Iterator[tuple[Any, dict[str, Any]]], dict[str, Any]]:
    try:
        import shapefile
        from shapely.geometry import shape
    except ImportError as error:
        raise RuntimeError("pyshp and shapely are required by geospatial-worker") from error
    destination = workdir / "shapefile"
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        members = archive.infolist()
        paths = [Path(member.filename.replace("\\", "/")) for member in members]
        if any(path.anchor or not path.parts or ".." in path.parts or
               any(":" in part for part in path.parts) for path in paths):
            raise GeospatialDataError("geo_invalid_archive", "SHP 压缩包包含不安全路径，未解压数据。请重新打包一套 SHP 及其配套文件。")
        total = sum(member.file_size for member in members)
        reserve = 256 * 1024**2
        if total + reserve > shutil.disk_usage(destination).free:
            raise GeospatialDataError("geo_disk_budget", "项目磁盘空间不足，无法解压 SHP。请释放空间后重试。")
        completed = 0
        for member, path in zip(members, paths, strict=True):
            target = (destination / path).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise GeospatialDataError("geo_invalid_archive", "SHP 压缩包包含不安全路径，未解压数据。")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("xb") as output:
                while chunk := source.read(1024 * 1024):
                    if completed + len(chunk) > total or shutil.disk_usage(destination).free < reserve + len(chunk):
                        raise GeospatialDataError("geo_disk_budget", "项目磁盘空间不足或 SHP 压缩包解压大小不符。")
                    output.write(chunk)
                    completed += len(chunk)
                    report("unpacking_source", completed, total)
    shp_files = list(destination.rglob("*.shp"))
    if len(shp_files) != 1:
        raise GeospatialDataError("geo_single_dataset_required", "压缩包中应只有一套 SHP 数据，请分别导入多个图层。")
    shp_path = shp_files[0]
    for suffix in (".shx", ".dbf"):
        if not shp_path.with_suffix(suffix).is_file():
            raise GeospatialDataError("geo_companions_missing", f"SHP 缺少同名 {suffix} 配套文件，请补齐后重新导入。")
    def features():
        with shapefile.Reader(str(shp_path)) as reader:
            for index, shape_record in enumerate(reader.iterShapeRecords(), 1):
                geometry = shape(shape_record.shape.__geo_interface__)
                if geometry.geom_type not in {"Polygon", "MultiPolygon"}:
                    raise GeospatialDataError("geo_polygon_required", f"SHP 第 {index} 条要素不是面，请选择 Polygon/MultiPolygon 图层。")
                yield geometry, dict(shape_record.record.as_dict())
                report("preparing_regions", index, max(reader.numRecords, index))

    prj = shp_path.with_suffix(".prj")
    return features(), {
        "format": "shapefile",
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


def _required_text(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value.strip()
