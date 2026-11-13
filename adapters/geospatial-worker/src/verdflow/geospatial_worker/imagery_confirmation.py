"""Geospatial refinement of a frozen candidate selection, prior to Core publication."""

from __future__ import annotations

from contextlib import closing, ExitStack, nullcontext
import errno
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


_REFINEMENT_PROGRESS = {"refining": (0.0, 1.0)}


def _wait_for_progress(process, path, check, timeout_seconds):
    from verdflow.geospatial_worker.supervisor import _worker_progress

    last_completed, last_progress = 0, monotonic()
    while process.poll() is None:
        check()
        current = _worker_progress(path, _REFINEMENT_PROGRESS)
        now = monotonic()
        if current is not None and current[1] > last_completed:
            last_completed, last_progress = current[1], now
        if now - last_progress >= timeout_seconds:
            raise ValueError("影像细化长时间没有处理进展，已停止。原有种植范围未更改，可缩小选区后重试。")
        yield
    check()


def refine_selection(store, python, result, options, check, *, timeout_seconds=300.0):
    """Yield while the isolated Adapter computes geometry. Only the caller may publish it."""
    from verdflow.project_store import ProjectQueryStore
    from verdflow.worker_runtime import ProcessTreeGuard
    from verdflow.geospatial_worker.supervisor import _IMAGERY_MEMORY_POOL, _minimal_environment

    outputs = {entry["role"]: entry for entry in result["output_artifacts"]}
    if "vegetation_mask" not in outputs:
        raise ValueError("此影像候选缺少植被遮罩，无法应用细化。")
    with store.open_artifact(outputs["source_manifest"]["artifact_id"]) as stream:
        manifest = json.load(stream)
    imagery = manifest["imagery"]
    member = next((member for member in store.list_source_assembly_members(result["source_id"])
                   if member.source_member_id == imagery["source_member_id"]), None)
    if member is None:
        raise ValueError("无法找到原始影像。")
    source = store.get_source(member.source_id)
    if source.content_hash != imagery["content_hash"] or source.content_hash != member.source_content_hash:
        raise ValueError("原始影像与识别候选不一致。")
    with ProjectQueryStore.open(store.root) as query:
        image = query.local_artifact_path(source.artifact_id)
        regions = query.local_artifact_path(outputs["regions"]["artifact_id"])
        mask = query.local_artifact_path(outputs["vegetation_mask"]["artifact_id"])
    with TemporaryDirectory(dir=store.staging_path, prefix="imagery-refine-") as tmp, ExitStack() as cleanup:
        work = Path(tmp)
        request = {"image": str(image), "regions": str(regions), "mask": str(mask), "grid": imagery, "options": options}
        (work / "input.json").write_text(json.dumps(request), encoding="utf-8")
        limit = None
        if sys.platform == "win32":
            while True:
                check()
                reservation = _IMAGERY_MEMORY_POOL.try_reserve()
                if reservation is not None:
                    cleanup.callback(reservation.close)
                    limit = reservation.limit_bytes
                    break
                yield
        guard = ProcessTreeGuard(memory_limit_bytes=limit)
        process = None
        try:
            process = subprocess.Popen([str(python), "-I", "-B", "-X", "utf8", __file__, str(work)],
                cwd=work, env=_minimal_environment(work), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, **guard.popen_options())
            try:
                guard.attach(process)
            except Exception:
                process.kill()
                process.wait()
                raise
            yield from _wait_for_progress(process, work / "progress.json", check, timeout_seconds)
            if process.returncode != 0:
                message = work / "error.txt"
                raise ValueError(message.read_text(encoding="utf-8") if message.is_file()
                                 else "影像细化未完成，原有种植范围未更改。")
        finally:
            try:
                if process is not None and process.poll() is None:
                    guard.terminate(process, grace_seconds=2.0)
            finally:
                guard.close()
                cleanup.close()
        for role, filename in (("regions", "regions.json"), ("geometry_metrics", "region-metrics.json")):
            previous = store.get_artifact(outputs[role]["artifact_id"])
            artifact = store.put_file(work / filename, media_type=previous.media_type, schema_id=previous.schema_id,
                                      schema_version=previous.schema_version, on_chunk=check)
            outputs[role] = {**outputs[role], "artifact_id": artifact.artifact_id,
                             "content_hash": artifact.content_hash, "byte_size": artifact.byte_size}
    return {**result, "output_artifacts": [outputs[role] for role in ("source_manifest", "regions", "geometry_metrics")]}


def _run(work: Path) -> None:
    import numpy as np
    import rasterio
    from affine import Affine
    from rasterio.features import rasterize
    from rasterio.windows import Window
    from shapely import get_num_coordinates
    from shapely.geometry import Polygon, box
    from threading import Event

    from verdflow.geospatial_worker.imagery import _candidate_metadata, _projected_regions
    from verdflow.geospatial_worker.imagery_output import _json, _ordered_regions, _write_large_region
    from verdflow.geospatial_worker.imagery_paint import paint_polygon_centers
    from verdflow.geospatial_worker.imagery_regions import RegionTally, polygon_tiles
    from verdflow.geospatial_worker.imagery_tile_index import DiskTileRegions
    from verdflow.geospatial_worker.imagery_tiles import TILE_SIZE, _LOCAL_REFINEMENT_MINIMUM, _index_proposals, _read_proposals

    last_check, completed = float("-inf"), 0

    def check(*_args):
        nonlocal last_check
        now = monotonic()
        if now - last_check >= 1:
            last_check = now
            if shutil.disk_usage(work).free < 64 * 1024**2:
                raise OSError(errno.ENOSPC, "Insufficient space for imagery refinement")

    def advance(*_args):
        nonlocal completed
        completed += 1
        previous = last_check
        check()
        if last_check != previous:
            try:
                (work / "progress.json").write_text(json.dumps(["refining", completed, sys.maxsize]), encoding="ascii")
            except PermissionError:
                pass  # The supervisor may briefly have the file open on Windows.

    def paint(index, shape, transform, footprint):
        values = np.zeros(shape, dtype="uint32")
        batch = []
        vertices = 0
        with closing(index.matches(footprint)) as matches:
            for _, polygon in matches:
                size = int(get_num_coordinates(polygon))
                if batch and (size + vertices > 80_000 or len(batch) >= 1024):
                    rasterize(batch, out=values, transform=transform, skip_invalid=False)
                    batch.clear()
                    vertices = 0
                if size > 80_000:
                    paint_polygon_centers(values, transform, polygon, 1, on_chunk=advance)
                else:
                    batch.append((polygon, 1))
                    vertices += size
                advance()
        if batch:
            rasterize(batch, out=values, transform=transform, skip_invalid=False)
        return values != 0

    data = json.loads((work / "input.json").read_text(encoding="utf-8"))
    grid, options = data["grid"], data["options"]
    mode, strength, minimum = options["mode"], options["strength"], int(options["minimum"])
    min_region = int(options["min_region_pixels"])
    if mode not in ("greenness", "patches") or type(strength) is not int or not 0 <= strength <= 100 or min(minimum, min_region) < 0:
        raise ValueError("Invalid imagery refinement options")
    bands = tuple(grid["rgb_bands"])
    cut = work / "retained.tif"
    check()
    with rasterio.Env(GDAL_CACHEMAX=32 * 1024**2, GDAL_NUM_THREADS="1", GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
                      GDAL_PAM_ENABLED="NO"), rasterio.open(data["image"], driver="GTiff", sharing=False) as image, \
            rasterio.open(data["mask"], driver="GTiff", sharing=False) as mask, closing(
                DiskTileRegions.build(work / "selection.sqlite", Path(data["regions"]), None, advance),
            ) as selection:
        if (image.shape != (grid["height"], grid["width"]) or tuple(image.transform[:6]) != tuple(grid["transform"])
                or mask.shape != image.shape or mask.transform != image.transform or mask.crs != image.crs or mask.count != 1):
            raise ValueError("原始影像和植被遮罩的网格不一致。")
        use_index = _LOCAL_REFINEMENT_MINIMUM < minimum <= image.width * image.height
        if use_index:
            _index_proposals(data["image"], data["mask"], bands, (mode, strength, minimum), work, Event(), progress=advance)
        with (closing(DiskTileRegions(work / "overlay.sqlite")) if use_index else nullcontext()) as proposals, \
                rasterio.open(cut, "w", driver="GTiff", width=image.width, height=image.height, count=1, dtype="uint8",
                              transform=image.transform, crs=image.crs, tiled=True, blockxsize=TILE_SIZE, blockysize=TILE_SIZE,
                              compress="deflate", BIGTIFF="IF_SAFER", SPARSE_OK=True) as output:
            block = TILE_SIZE * 4
            for y in range(0, image.height, block):
                for x in range(0, image.width, block):
                    w, h = min(block, image.width - x), min(block, image.height - y)
                    window = Window(x, y, w, h)
                    candidate = mask.read(1, window=window) == 1
                    advance()
                    if not candidate.any():
                        continue
                    transform = image.window_transform(window)
                    footprint = Polygon([transform * point for point in ((0, 0), (w, 0), (w, h), (0, h))])
                    kept = candidate & paint(selection, (h, w), transform, footprint)
                    if not kept.any():
                        continue
                    if proposals is not None:
                        kept &= ~paint(proposals, (h, w), Affine.translation(x, y), box(x, y, x+w, y+h))
                    elif minimum <= _LOCAL_REFINEMENT_MINIMUM:
                        kept &= ~_read_proposals(image, mask, bands, x, y, w, h, mode, strength, minimum)
                    if kept.any():
                        output.write(kept.astype(np.uint8), 1, window=window)
        tally = RegionTally()
        with rasterio.open(cut) as retained, closing(
            polygon_tiles(retained, work / "retained.sqlite", min_region, tally, advance),
        ) as components, _ordered_regions(
            _projected_regions(components, image.transform, tally), work / "output.sqlite",
        ) as (count, rows), (work / "regions.json").open("w", encoding="utf-8") as geometry, \
                (work / "region-metrics.json").open("w", encoding="utf-8") as metrics:
            for stream, schema in ((geometry, "verdflow.regions"), (metrics, "verdflow.region-metrics")):
                header = _json({"schema": schema, "version": "1", "coordinate_space": "source_scene_world",
                                "offset_applied": False, "region_count": count})
                stream.write(header[:-1] + ', "regions": [')
            for index, (_, polygon, pixels) in enumerate(rows, 1):
                advance()
                row, metric = _candidate_metadata(index, polygon, pixels, grid["source_member_id"])
                if index > 1:
                    geometry.write(",")
                    metrics.write(",")
                _write_large_region(geometry, row, polygon, on_chunk=advance)
                metrics.write(_json(metric))
            geometry.write("]}")
            metrics.write("]}")


def _main(work: Path) -> None:
    from verdflow.geospatial_worker.errors import GeospatialDataError, is_host_memory_error
    try:
        _run(work)
    except Exception as error:
        if isinstance(error, GeospatialDataError) and error.code in {"imagery_all_regions_filtered", "imagery_no_vegetation"}:
            message = "细化后没有可确认的区域。请减弱留白、调低最小区域或扩大选择。"
        elif isinstance(error, GeospatialDataError):
            message = str(error)
        elif getattr(error, "errno", None) == errno.ENOSPC:
            message = "项目磁盘空间不足，未更新种植范围。"
        elif is_host_memory_error(error):
            message = "影像细化内存不足，未更新种植范围。请释放内存或缩小选区后重试。"
        else:
            message = "无法完成影像细化，请检查影像与候选区域。原有种植范围未更改。"
        (work / "error.txt").write_text(message, encoding="utf-8")
        raise


if __name__ == "__main__":
    _main(Path(sys.argv[1]))
