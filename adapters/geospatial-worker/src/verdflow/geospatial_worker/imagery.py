"""Bounded, local RGB segmentation for the experimental imagery Region Source."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import closing, suppress
import hashlib
from itertools import chain, pairwise
import json
import math
import os
from pathlib import Path
import shutil
from time import monotonic
from typing import TYPE_CHECKING, Any

from .errors import GeospatialDataError, is_host_memory_error
from .imagery_regions import RegionTally, polygon_tiles
from .processing import _ring_xyz
from .protocol import IMAGERY_MODEL_PROFILE, IMAGERY_REGION_RECIPE_ID, ImageryRegionOptions

if TYPE_CHECKING:
    from numpy import ndarray
    from rasterio.io import DatasetReader
    from shapely.geometry import Polygon


MODEL_SHA256 = "58db911702831c05889a1dd5354011521b6de7bd768ab5f8cff70d4f52b0877d"
MODEL_BYTES = 15_028_742
PATCH = 512
STRIDE = 384
CLASS_COUNT = 9
VEGETATION_CLASSES = (2, 5, 7)
RGB_MEAN = (0.485, 0.456, 0.406)
RGB_STD = (0.229, 0.224, 0.225)
BLEND_WIDTH = 4096
MASK_BLOCK = 256
MAX_DECODE_BLOCK_BYTES = 128 * 1024 * 1024
MIN_FREE_DISK_BYTES = 256 * 1024 * 1024
MAX_PREVIEW_VERTICES = 60_000
PREVIEW_MAX_DIMENSION = 1600


def prepare_imagery_regions(
    command: dict[str, Any], output_dir: Path,
    report: Callable[[str, int, int], None] | None = None,
    *, write_candidate: bool = False,
) -> dict[str, Any]:
    import rasterio
    from rasterio.errors import RasterioIOError
    from rasterio.windows import Window

    options = ImageryRegionOptions.from_document(command.get("options"))
    source, = command["sources"]
    mask_path = output_dir / "vegetation-mask.tif"
    scratch = output_dir / "seams.sqlite"
    tally = RegionTally()
    last_check = float("-inf")

    def progress(phase: str, completed: int, total: int) -> None:
        nonlocal last_check
        now = monotonic()
        if now - last_check >= 1:
            _require_disk(output_dir)
            last_check = now
        if report is not None:
            report(phase, completed, total)

    try:
        with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_CACHEMAX=64 * 1024 * 1024):
            with rasterio.open(source["source_path"], driver="GTiff") as dataset:
                _validate_grid(dataset, options.rgb_bands)
                if options.elevation_member_id is not None:
                    _validate_elevation(dataset, Path(command["elevation_path"]))
                width, height = dataset.width, dataset.height
                bounds, transform, crs = list(dataset.bounds), dataset.transform, dataset.crs
                checkpoint = Path(command["mask_checkpoint"]) if "mask_checkpoint" in command else None
                runtime_version = _checkpoint_runtime(checkpoint, source, options, dataset)
                if runtime_version is None:
                    # Reserve room for the scratch mask and its project Artifact.
                    padded_pixels = math.ceil(width / MASK_BLOCK) * math.ceil(height / MASK_BLOCK) * MASK_BLOCK**2
                    _require_disk(output_dir, padded_pixels * 2)
                    progress("loading_model", 0, 1)
                    predict, runtime_version = _load_predictor(Path(command["model_path"]), options)
                    with rasterio.open(mask_path, "w", driver="GTiff", width=width, height=height, count=1,
                                       dtype="uint8", crs=crs, transform=transform, nodata=255,
                                       compress="deflate", tiled=True, blockxsize=MASK_BLOCK,
                                       blockysize=MASK_BLOCK, BIGTIFF="IF_SAFER") as result:
                        for x, y, pixels in _mask_windows(dataset, options.rgb_bands, predict, progress):
                            result.write(pixels, 1, window=Window(x, y, pixels.shape[1], pixels.shape[0]))
                        if checkpoint is not None:
                            result.update_tags(
                                verdflow_checkpoint=_mask_checkpoint_key(source, options, runtime_version),
                            )
                    if checkpoint is not None:
                        with mask_path.open("rb+") as completed_mask:
                            os.fsync(completed_mask.fileno())
                        os.replace(mask_path, checkpoint)
                if checkpoint is not None:
                    try:
                        os.link(checkpoint, mask_path)
                    except OSError:
                        _require_disk(output_dir, checkpoint.stat().st_size)
                        shutil.copyfile(checkpoint, mask_path)
                progress("building_preview", 0, 1)
                preview = _write_preview(dataset, options.rgb_bands, output_dir / "imagery-preview.png")
            try:
                with rasterio.open(mask_path) as mask:
                    with closing(polygon_tiles(mask, scratch, options.min_region_pixels, tally, progress)) as polygons:
                        if write_candidate:
                            from .imagery_output import write_imagery_candidate

                            review = write_imagery_candidate(
                                output_dir, _projected_regions(polygons, transform, tally),
                                options.imagery_member_id, preview, progress,
                            )
                        else:
                            rows, metrics = _region_rows(polygons, transform, options.imagery_member_id, tally)
            except RasterioIOError:
                if checkpoint is not None:
                    with suppress(OSError):
                        checkpoint.unlink(missing_ok=True)
                raise
        if not write_candidate:
            progress("writing_result", 0, 1)
        _require_disk(output_dir, mask_path.stat().st_size)
    except RasterioIOError as error:
        if is_host_memory_error(error):
            raise
        _require_disk(output_dir)
        raise GeospatialDataError("imagery_unreadable", "无法读取影像 GeoTIFF，请检查文件格式和完整性。") from error
    finally:
        # The supervisor removes the attempt directory after the worker exits.
        with suppress(OSError):
            scratch.unlink(missing_ok=True)

    epsg = crs.to_epsg()
    return {
        "source_manifest": {
            "schema": "verdflow.region-source-manifest",
            "version": "1",
            "source_id": command["source_id"],
            "recipe_id": IMAGERY_REGION_RECIPE_ID,
            "elevation_member_id": options.elevation_member_id,
            "preview": preview,
            "alignment": "user_supplied",
            "crs_validation": "imagery_projected_meters_only",
            "reprojection": "not_performed",
            "coordinate_frame": {
                "name": "Imagery projected coordinates",
                "handedness": "right",
                "up_axis": "+Z",
                "linear_unit": "meter",
                "crs_kind": "epsg" if epsg is not None else "wkt",
                "crs_definition": f"EPSG:{epsg}" if epsg is not None else crs.to_wkt(),
                "local_origin": [0.0, 0.0, 0.0],
                "offset_applied": False,
            },
            "imagery": {
                "source_member_id": options.imagery_member_id,
                "content_hash": source["source_content_hash"],
                "width": width, "height": height,
                "bounds": bounds, "transform": list(transform[:6]),
                "observed_crs": str(crs), "rgb_bands": list(options.rgb_bands),
            },
            "recognition": {
                "profile": IMAGERY_MODEL_PROFILE, "onnx_sha256": MODEL_SHA256,
                "runtime": "onnxruntime", "runtime_version": runtime_version,
                "backend": options.backend, "device_id": options.device_id,
                "rgb_mean": RGB_MEAN, "rgb_std": RGB_STD, "rgb_range": [0, 255],
                "patch": PATCH, "stride": STRIDE, "vegetation_classes": VEGETATION_CLASSES,
                "blend": "floored-hann-probabilities", "geometry": "pixel-boundary-4-connected",
                "min_region_pixels": options.min_region_pixels, "filtered_regions": tally.filtered,
            },
        },
    } | ({"review": review} if write_candidate else {
        "regions": _region_document("verdflow.regions", rows),
        "geometry_metrics": _region_document("verdflow.region-metrics", metrics),
    })


def _mask_checkpoint_key(
    source: dict[str, Any], options: ImageryRegionOptions, runtime_version: str,
) -> str:
    # Vectorization thresholds do not affect the inference mask.
    return hashlib.sha256(json.dumps([
        1, IMAGERY_MODEL_PROFILE, MODEL_SHA256, runtime_version,
        source["source_content_hash"], options.rgb_bands, options.backend,
        options.device_id, PATCH, STRIDE, RGB_MEAN, RGB_STD, VEGETATION_CLASSES,
    ], separators=(",", ":")).encode("ascii")).hexdigest()


def _checkpoint_runtime(
    path: Path | None, source: dict[str, Any],
    options: ImageryRegionOptions, dataset: DatasetReader,
) -> str | None:
    import rasterio
    from rasterio.errors import RasterioIOError

    if path is None or path.is_symlink() or not path.is_file():
        return None
    try:
        import onnxruntime

        runtime_version = onnxruntime.__version__
        key = _mask_checkpoint_key(source, options, runtime_version)
        with rasterio.open(path, driver="GTiff") as mask:
            tags = mask.tags()
            if (
                tags.get("verdflow_checkpoint") == key
                and mask.shape == dataset.shape
                and mask.transform == dataset.transform
                and mask.crs == dataset.crs
                and mask.dtypes == ("uint8",)
                and mask.nodata == 255
            ):
                return runtime_version
    except (RasterioIOError, ImportError, OSError):
        pass
    return None


def _validate_elevation(dataset: DatasetReader, path: Path) -> None:
    import rasterio
    from rasterio.errors import RasterioIOError

    try:
        with rasterio.open(path, driver="GTiff") as terrain:
            if (terrain.driver != "GTiff" or terrain.count != 1 or terrain.crs != dataset.crs
                    or terrain.transform.is_identity or terrain.transform.determinant == 0
                    or not all(math.isfinite(value) for value in terrain.transform[:6])):
                raise GeospatialDataError("imagery_elevation_grid_invalid", "请选择与影像使用相同投影、具有有效定位的单波段高程 GeoTIFF。")
            a, b = dataset.bounds, terrain.bounds
            if min(a.right, b.right) <= max(a.left, b.left) or min(a.top, b.top) <= max(a.bottom, b.bottom):
                raise GeospatialDataError("imagery_elevation_no_overlap", "影像与高程覆盖范围不相交，请先在 GIS 中对齐。")
    except RasterioIOError as error:
        raise GeospatialDataError("imagery_elevation_unreadable", "无法读取所选高程 GeoTIFF。") from error


def _write_preview(dataset: DatasetReader, bands: tuple[int, int, int], path: Path) -> dict[str, object]:
    import rasterio
    from rasterio.enums import Resampling
    from affine import Affine

    scale = min(1, PREVIEW_MAX_DIMENSION / max(dataset.width, dataset.height))
    width, height = max(1, round(dataset.width * scale)), max(1, round(dataset.height * scale))
    rgb = dataset.read(bands, out_shape=(3, height, width), resampling=Resampling.bilinear,
                       masked=True).filled(0).clip(0, 255).astype("uint8")
    with rasterio.open(path, "w", driver="PNG", count=3, width=width, height=height, dtype="uint8") as preview:
        preview.write(rgb)
    transform = dataset.transform @ Affine.scale(dataset.width / width, dataset.height / height)
    return {"width": width, "height": height, "transform": list(transform[:6])}


def _validate_grid(dataset: DatasetReader, bands: tuple[int, int, int]) -> None:
    import numpy as np

    if dataset.driver != "GTiff" or max(bands) > dataset.count or any(
        np.dtype(dataset.dtypes[band - 1]).kind not in "ui" for band in bands
    ):
        raise GeospatialDataError("imagery_rgb_required", "请选择含有效 RGB 整数波段的 GeoTIFF 影像。")
    block_rows, block_cols = (max(values) for values in zip(*dataset.block_shapes, strict=True))
    band_bytes = [np.dtype(dtype).itemsize for dtype in dataset.dtypes]
    bytes_per_pixel = sum(band_bytes) if dataset.profile.get("interleave") == "pixel" else max(band_bytes)
    if block_rows * block_cols * bytes_per_pixel > MAX_DECODE_BLOCK_BYTES:
        raise GeospatialDataError("imagery_decode_block_limit", "GeoTIFF 的单个解码块过大。请重新导出为分块 GeoTIFF（例如 512 × 512 块），无需降低分辨率。")
    crs = dataset.crs
    if (crs is None or not crs.is_projected or not math.isclose(crs.linear_units_factor[1], 1.0)
            or dataset.transform.is_identity
            or not all(math.isfinite(value) for value in dataset.transform[:6])
            or not math.isfinite(dataset.transform.determinant) or dataset.transform.determinant == 0
            or not all(math.isfinite(value) for value in dataset.bounds)):
        raise GeospatialDataError("imagery_grid_invalid", "影像须带有效的米制投影与栅格定位；VerdFlow 不会自动重投影。")


def _require_disk(directory: Path, required: int = 0) -> None:
    total = required + MIN_FREE_DISK_BYTES
    if shutil.disk_usage(directory).free < total:
        mib = math.ceil(total / 1024**2)
        raise GeospatialDataError("imagery_disk_budget", f"项目磁盘空间不足，本阶段至少需要 {mib} MiB 可用空间。请释放空间后重试。")


def _load_predictor(path: Path, options: ImageryRegionOptions) -> tuple[Callable[[ndarray], ndarray], str]:
    try:
        with path.open("rb") as stream:
            model = stream.read(MODEL_BYTES + 1)
    except OSError as error:
        raise GeospatialDataError("imagery_model_missing", "无法读取已配置的影像识别模型。") from error
    if len(model) != MODEL_BYTES or hashlib.sha256(model).hexdigest() != MODEL_SHA256:
        raise GeospatialDataError("imagery_model_mismatch", "影像模型与已验证版本不一致，请检查模型来源。")
    try:
        import onnxruntime as ort
    except (ImportError, OSError) as error:
        raise GeospatialDataError("imagery_runtime_missing", "影像 Worker 未配置可用的 ONNX Runtime。") from error

    provider = "DmlExecutionProvider" if options.backend == "directml" else "CPUExecutionProvider"
    if provider not in ort.get_available_providers():
        raise GeospatialDataError("imagery_backend_unavailable", "所选推理设备不可用，请检查运行环境或改用 CPU。")
    session_options = ort.SessionOptions()
    session_options.intra_op_num_threads = 4
    session_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    session_options.enable_mem_pattern = False
    providers = ([(provider, {"device_id": str(options.device_id)}), "CPUExecutionProvider"]
                 if options.backend == "directml" else [provider])
    try:
        session = ort.InferenceSession(model, sess_options=session_options, providers=providers)
    except Exception as error:
        if is_host_memory_error(error):
            raise
        raise GeospatialDataError("imagery_backend_init_failed", "影像推理设备初始化失败，请检查驱动或改用 CPU。") from error
    if session.get_providers()[0] != provider:
        raise GeospatialDataError("imagery_backend_unavailable", "所选推理设备无法启动，本次未自动切换后端。")
    session.disable_fallback()

    def predict(pixels: ndarray) -> ndarray:
        try:
            return session.run(["probabilities"], {"pixel_values": pixels[None]})[0][0]
        except Exception as error:
            if is_host_memory_error(error):
                raise
            raise GeospatialDataError("imagery_inference_failed", "影像推理中断，请检查显存或改用 CPU 后重试。") from error

    return predict, ort.__version__


def _positions(length: int, left: int = 0, right: int | None = None) -> Iterator[int]:
    last = max(0, length - PATCH)
    right = length if right is None else right
    first = max(0, (left - PATCH) // STRIDE + 1) * STRIDE
    yield from range(first, min(right - 1, last) + 1, STRIDE)
    if last % STRIDE and left < last + PATCH and last < right:
        yield last


def _mask_windows(
    dataset: DatasetReader, bands: tuple[int, int, int], predict: Callable[[ndarray], ndarray],
    progress: Callable[[str, int, int], None],
) -> Iterator[tuple[int, int, ndarray]]:
    import numpy as np
    from rasterio.windows import Window

    width, height = dataset.width, dataset.height
    stripe_height = min(PATCH, height)
    axis = np.maximum(np.hanning(PATCH).astype(np.float32), 0.05)
    blend = axis[:, None] * axis[None, :]
    mean = np.asarray(RGB_MEAN, dtype=np.float32)[:, None, None]
    std = np.asarray(RGB_STD, dtype=np.float32)[:, None, None]
    total = math.ceil(width / BLEND_WIDTH) * (math.ceil(max(0, height - PATCH) / STRIDE) + 1)
    completed = 0
    progress("inferring", 0, total)
    # Keep the original global tile origins and accumulation order at strip boundaries.
    for left in range(0, width, BLEND_WIDTH):
        right = min(width, left + BLEND_WIDTH)
        strip_width = right - left
        sums = np.zeros((CLASS_COUNT, stripe_height, strip_width), dtype=np.float32)
        weights = np.zeros((stripe_height, strip_width), dtype=np.float32)
        for y, next_y in pairwise(chain(_positions(height), (height,))):
            for x in _positions(width, left, right):
                w, h = min(PATCH, width - x), min(PATCH, height - y)
                window = Window(x, y, w, h)
                rgb = dataset.read(bands, window=window)
                valid = np.all(dataset.read_masks(bands, window=window) != 0, axis=0)
                if not valid.any():
                    continue
                if np.any(((rgb < 0) | (rgb > 255)) & valid[None]):
                    raise GeospatialDataError("imagery_rgb_range", "影像有效 RGB 值不在 0–255，请先转换为可见光 RGB 影像。")
                rgb[:, ~valid] = 0
                pixels = np.pad(rgb.astype(np.float32), ((0, 0), (0, PATCH - h), (0, PATCH - w)), mode="edge")
                probabilities = predict((pixels / np.float32(255) - mean) / std)
                if probabilities.shape != (CLASS_COUNT, PATCH, PATCH) or not np.isfinite(probabilities).all():
                    raise GeospatialDataError("imagery_output_invalid", "影像模型返回了无效的分类结果。")
                start, stop = max(left, x), min(right, x + w)
                tile_slice = slice(start - x, stop - x)
                strip_slice = slice(start - left, stop - left)
                weight = blend[:h, tile_slice]
                sums[:, :h, strip_slice] += probabilities[:, :h, tile_slice] * weight[None]
                weights[:h, strip_slice] += weight

            rows = next_y - y
            valid = np.all(dataset.read_masks(bands, window=Window(left, y, strip_width, rows)) != 0, axis=0)
            labels = (sums[:, :rows] / np.where(weights[:rows] > 0, weights[:rows], np.float32(1))).argmax(axis=0)
            yield left, y, np.where(valid, np.isin(labels, VEGETATION_CLASSES), 255).astype(np.uint8)
            completed += 1
            progress("inferring", completed, total)
            if next_y < height:
                remaining = stripe_height - rows
                sums[:, :remaining] = sums[:, rows:]
                weights[:remaining] = weights[rows:]
                sums[:, remaining:] = 0
                weights[remaining:] = 0


def _region_rows(
    polygons: Iterator[tuple[Polygon, int]], transform: Any, member_id: str, tally: RegionTally,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ordered = sorted(_projected_regions(polygons, transform, tally), key=lambda item: item[0])
    rows, metrics = [], []
    for index, (_, polygon, pixels) in enumerate(ordered, 1):
        row, metric = _candidate_row(index, polygon, pixels, member_id)
        rows.append(row)
        metrics.append(metric)
    return rows, metrics


def _projected_regions(
    polygons: Iterator[tuple[Polygon, int]], transform: Any, tally: RegionTally,
) -> Iterator[tuple[tuple[float, float, int], Polygon, int]]:
    from shapely.affinity import affine_transform

    index = 0
    for index, (pixel_polygon, pixels) in enumerate(polygons, 1):
        polygon = affine_transform(pixel_polygon, [transform.a, transform.b, transform.d, transform.e, transform.c, transform.f])
        if not polygon.is_valid or not math.isfinite(polygon.area) or polygon.area <= 0:
            raise GeospatialDataError("imagery_invalid_geometry", "影像转面得到无效边界，本次未自动填洞或修复。")
        min_x, min_y, _, _ = polygon.bounds
        yield (min_x, min_y, index), polygon, pixels
    if index == 0:
        if tally.filtered:
            raise GeospatialDataError("imagery_all_regions_filtered", f"{tally.filtered} 个识别区域均小于最小区域像素数，请调低阈值后重试。")
        raise GeospatialDataError("imagery_no_vegetation", "影像中未识别出可用的植被区域，请检查波段和影像内容。")


def _candidate_row(index: int, polygon: Polygon, pixels: int, member_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    row, metric = _candidate_metadata(index, polygon, pixels, member_id)
    row["footprint_boundary_xyz"] = [_ring_xyz(ring.coords) for ring in (polygon.exterior, *polygon.interiors)]
    return row, metric


def _candidate_metadata(index: int, polygon: Polygon, pixels: int, member_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    min_x, min_y, max_x, max_y = polygon.bounds
    identity = {"id": f"region_{index:05d}", "source_member_id": member_id}
    return (
        {**identity, "label": f"区域 {index}", "tags": "", "pixel_count": pixels,
         "bbox": [min_x, min_y, 0.0, max_x, max_y, 0.0]},
        {**identity, "footprint_area_xy": polygon.area,
         "boundary_perimeter_xy": polygon.length, "hole_count": len(polygon.interiors)},
    )


def _region_document(schema: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"schema": schema, "version": "1", "coordinate_space": "source_scene_world",
            "offset_applied": False, "region_count": len(rows), "regions": rows}


def imagery_review_pages(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    pages: list[list[dict[str, Any]]] = []
    page: list[dict[str, Any]] = []
    vertices = 0
    for original in rows:
        row = _imagery_preview_row(original)
        count = sum(map(len, row["footprint_boundary_xyz"]))
        if page and (len(page) >= 256 or vertices + count > MAX_PREVIEW_VERTICES):
            pages.append(page)
            page, vertices = [], 0
        page.append(row)
        vertices += count
    if page:
        pages.append(page)
    return pages


def write_imagery_review_overview(
    path: Path, preview: dict[str, Any], exact_rows: list[dict[str, Any]],
) -> None:
    """Encode candidate ordinals, not simplified review boundaries, on the preview grid."""
    from affine import Affine
    import rasterio
    from rasterio.features import rasterize

    width, height = preview["width"], preview["height"]
    ids = rasterize(
        (({"type": "Polygon", "coordinates": row["footprint_boundary_xyz"]}, ordinal)
         for ordinal, row in enumerate(exact_rows, 1)),
        out_shape=(height, width),
        transform=Affine(*preview["transform"]),
        dtype="uint32",
    )
    with rasterio.open(path, "w", driver="PNG", width=width, height=height, count=3, dtype="uint8") as output:
        for band in range(3):
            output.write(((ids >> (band * 8)) & 255).astype("uint8"), band + 1)


def _imagery_preview_row(row: dict[str, Any]) -> dict[str, Any]:
    from shapely.geometry import Polygon

    rings = row["footprint_boundary_xyz"]
    if sum(map(len, rings)) <= MAX_PREVIEW_VERTICES:
        return row
    outline = []
    # Some geometries cannot fit even with a triangle per hole. They stay selectable
    # in the list instead of drawing a misleading filled bounding box.
    if len(rings) * 4 <= MAX_PREVIEW_VERTICES:
        try:
            outline = _simplified_preview_rings(Polygon(rings[0], rings[1:]))
        except GEOSException:
            pass
    return {**row, "footprint_boundary_xyz": outline, "preview_mode": "simplified" if outline else "list_only"}


def _simplified_preview_rings(polygon: Polygon) -> list[list[list[float]]]:
    from shapely import get_num_coordinates
    from shapely.errors import GEOSException

    if (len(polygon.interiors) + 1) * 4 > MAX_PREVIEW_VERTICES:
        return []
    x0, y0, x1, y1 = polygon.bounds
    tolerance = max(x1 - x0, y1 - y0) / PREVIEW_MAX_DIMENSION
    try:
        for _ in range(4):
            simplified = polygon.simplify(tolerance, preserve_topology=True)
            if get_num_coordinates(simplified) <= MAX_PREVIEW_VERTICES:
                return [_ring_xyz(ring.coords) for ring in (simplified.exterior, *simplified.interiors)]
            tolerance *= 4
    except GEOSException:
        pass
    return []
