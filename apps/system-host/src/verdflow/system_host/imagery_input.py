"""Built-in Studio imagery preparation, review and explicit terrain binding."""

from __future__ import annotations

import base64
from contextlib import closing
from dataclasses import dataclass
import json
import math
from pathlib import Path
import struct
from typing import Any, Iterable, TYPE_CHECKING

from verdflow.contracts import JobInfo, JobStatus, ResolvedPlantWorkflow, SamplingSourceRef, SceneRevisionInfo, SourceKind
from verdflow.core import REGION_SOURCE_CONFIRM_ACTIVE, REGION_SOURCE_CONFIRM_COMMAND, RegionSourceRequest, region_source_revision_lock
from verdflow.geospatial_worker import (
    IMAGERY_PREPARE_REGIONS_COMMAND, IMAGERY_REGION_PROVIDER_ID, IMAGERY_REGION_RECIPE_ID,
    IMAGERY_REVIEW_INDEX_ENCODING, IMAGERY_REVIEW_INDEX_ROW, IMAGERY_REVIEW_ORDINAL_LIMIT, ImageryRegionOptions,
)
from verdflow.project_store import ArtifactNotFoundError, ProjectQueryStore, ProjectStore

if TYPE_CHECKING:
    from verdflow.core_host import CoreHost


@dataclass(frozen=True, slots=True)
class ImageryCandidateRegion:
    region_id: str
    label: str
    bbox: tuple[float, ...]
    footprint_boundary_xyz: tuple[tuple[tuple[float, ...], ...], ...]
    pixel_count: int | None
    preview_mode: str


@dataclass(frozen=True, slots=True)
class ImageryReviewItem:
    region_id: str
    label: str
    pixel_count: int | None
    page: int


@dataclass(frozen=True, slots=True)
class ImageryCandidate:
    bounds: tuple[float, ...]
    preview_transform: tuple[float, ...]
    preview_png_base64: str | None
    regions: tuple[ImageryCandidateRegion, ...]
    page: int
    page_count: int
    region_count: int
    min_region_pixels: int
    filtered_regions: int
    preview_width: int
    preview_height: int
    overview_png_base64: str | None
    review_index: tuple[ImageryReviewItem, ...] | None
    source_grid: ImageryGrid | None
    confirmed_region_set_id: str | None
    confirmed_region_ids: tuple[str, ...] | None
    selection_locked_reason: str | None
    review_index_encoding: str | None = None
    confirmed_region_bits: str | None = None
    pending_confirmation_job_id: str | None = None
    refinement_available: bool = False
    confirmed_refinement: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ImageryReviewIndexBatch:
    start: int
    count: int
    rows_base64: str


@dataclass(frozen=True, slots=True)
class ImageryGrid:
    width: int
    height: int
    transform: tuple[float, ...]
    bands: tuple[int, ...]


def _source_grid(imagery: dict[str, Any]) -> ImageryGrid | None:
    width, height = imagery.get("width"), imagery.get("height")
    transform = imagery.get("transform", ())
    bands = imagery.get("rgb_bands", ())
    if (type(width) is not int or type(height) is not int or min(width, height) <= 0
            or not isinstance(transform, (list, tuple)) or len(transform) != 6
            or not all(isinstance(value, (float, int)) and math.isfinite(value) for value in transform)
            or not isinstance(bands, (list, tuple)) or len(bands) != 3
            or not all(type(band) is int and band > 0 for band in bands)):
        return None
    return ImageryGrid(width, height, tuple(transform), tuple(bands))


def resolve_imagery_tiles(store: ProjectQueryStore, job_id: str) -> tuple[Path, Path, ImageryGrid, int | None, Path | None] | None:
    outputs = _imagery_outputs(store, job_id)
    manifest = _document(store, outputs["source_manifest"])
    imagery = manifest["imagery"]
    grid = _source_grid(imagery)
    if grid is None:
        return None
    member = next((item for item in store.list_source_assembly_members(manifest["source_id"])
                   if item.source_member_id == imagery.get("source_member_id")), None)
    if member is None:
        return None
    source = store.get_source(member.source_id)
    artifact = store.get_artifact(source.artifact_id)
    if (source.kind is not SourceKind.GEOTIFF or not
            source.content_hash == member.source_content_hash == imagery.get("content_hash") == artifact.content_hash):
        return None
    try:
        image_path = store.local_artifact_path(source.artifact_id)
        regions_path = store.local_artifact_path(outputs["regions"])
    except (OSError, ValueError):
        return None
    mask_path = None
    if "vegetation_mask" in outputs:
        try:
            mask_path = store.local_artifact_path(outputs["vegetation_mask"])
        except (ArtifactNotFoundError, OSError, ValueError):
            pass
    count = (manifest.get("review") or {}).get("region_count")
    return image_path, regions_path, grid, count if type(count) is int and count > 0 else None, mask_path


def latest_imagery_job(store: ProjectStore | ProjectQueryStore) -> JobInfo | None:
    jobs = store.list_jobs(command_types=(IMAGERY_PREPARE_REGIONS_COMMAND,), limit=1).items
    return jobs[0] if jobs else None


def scene_imagery_job_id(store: ProjectQueryStore, scene: SceneRevisionInfo) -> str | None:
    if scene.importer_id != IMAGERY_REGION_PROVIDER_ID:
        return None
    job_id = _document(store, scene.scene_manifest_artifact_id).get("producer_job_id")
    return job_id if isinstance(job_id, str) and job_id else None


def imagery_request(store: ProjectStore, request: RegionSourceRequest, backend: str) -> RegionSourceRequest:
    if set(request.options) - {"min_region_pixels"} != {"imagery_source_id", "elevation_source_id"}:
        raise ValueError("请选择影像和高程来源。")
    minimum = request.options.get("min_region_pixels", 0)
    if type(minimum) is not int or not 0 <= minimum <= (1 << 53) - 1:
        raise ValueError("最小区域像素数必须是非负整数。")
    image_id, terrain_id = (request.options[key] for key in ("imagery_source_id", "elevation_source_id"))
    if not all(isinstance(value, str) and value.strip() for value in (image_id, terrain_id)) or image_id == terrain_id:
        raise ValueError("影像与高程必须来自两个不同的 GeoTIFF。")
    members = store.list_source_assembly_members(request.source_id)
    images = [member for member in members if member.source_id == image_id]
    terrains = [member for member in members if member.source_id == terrain_id]
    if len(images) != 1 or len(terrains) != 1:
        raise ValueError("影像或高程来源不属于当前项目的输入组合。")
    document = ImageryRegionOptions(images[0].source_member_id, backend=backend,
        elevation_member_id=terrains[0].source_member_id, min_region_pixels=minimum).to_document()
    return RegionSourceRequest(request.source_id, IMAGERY_REGION_PROVIDER_ID, document)


def _imagery_outputs(store: ProjectQueryStore | ProjectStore, job_id: str) -> dict[str, str]:
    job = store.get_job(job_id)
    if job.command.command_type != IMAGERY_PREPARE_REGIONS_COMMAND or job.status is not JobStatus.SUCCEEDED or not job.result_artifact_id:
        raise ValueError("当前任务没有可预览的影像区域。")
    result = _document(store, job.result_artifact_id)
    return {item["role"]: item["artifact_id"] for item in result["output_artifacts"]}


def included_after_exclusions(store: ProjectStore, job_id: str, excluded: tuple[str, ...]) -> tuple[str, ...]:
    outputs = _imagery_outputs(store, job_id)
    if "imagery_review_index" in outputs:
        all_ids = [row["region_id"] for row in _document(store, outputs["imagery_review_index"])["items"]]
    else:
        all_ids = [row["id"] for row in _document(store, outputs["regions"])["regions"]]
    removed = set(excluded)
    if len(removed) != len(excluded) or not removed.issubset(all_ids):
        raise ValueError("选择包含重复或不属于当前候选结果的区域。")
    return tuple(region_id for region_id in all_ids if region_id not in removed)


def _packed_index(store: ProjectStore | ProjectQueryStore, outputs: dict[str, str], review: dict[str, Any]) -> tuple[str, int]:
    count = review.get("region_count")
    artifact_id = outputs.get("imagery_packed_index")
    if review.get("index_encoding") != IMAGERY_REVIEW_INDEX_ENCODING or not artifact_id:
        raise ValueError("当前候选不支持分块索引。")
    artifact = store.get_artifact(artifact_id)
    if (type(count) is not int or not 0 < count < IMAGERY_REVIEW_ORDINAL_LIMIT
            or artifact.schema_id != "verdflow.imagery-ordinal-index" or artifact.schema_version != "1"
            or artifact.byte_size != count * IMAGERY_REVIEW_INDEX_ROW.size):
        raise ValueError("候选区域索引不完整，请重新识别。")
    return artifact_id, count


def read_imagery_index_batch(store: ProjectQueryStore, job_id: str, start: int) -> ImageryReviewIndexBatch:
    outputs = _imagery_outputs(store, job_id)
    review = _document(store, outputs["source_manifest"]).get("review") or {}
    artifact_id, total = _packed_index(store, outputs, review)
    if not 0 <= start < total:
        raise ValueError("候选索引起点超出范围。")
    count = min(total - start, 32_768)
    row_size = IMAGERY_REVIEW_INDEX_ROW.size
    with store.open_artifact(artifact_id) as stream:
        stream.seek(start * row_size)
        rows = stream.read(count * row_size)
    if len(rows) != count * row_size:
        raise ValueError("候选区域索引不完整，请重新识别。")
    return ImageryReviewIndexBatch(start, count, base64.b64encode(rows).decode("ascii"))


def included_from_imagery_bits(store: ProjectStore, job_id: str, encoded: str) -> tuple[str, ...]:
    outputs = _imagery_outputs(store, job_id)
    review = _document(store, outputs["source_manifest"]).get("review") or {}
    _, count = _packed_index(store, outputs, review)
    size = (count + 7) // 8
    if len(encoded) != 4 * ((size + 2) // 3):
        raise ValueError("选区大小与当前候选不一致，请重新打开影像阶段。")
    try:
        bits = base64.b64decode(encoded, validate=True)
    except ValueError as error:
        raise ValueError("候选区域选择无效。") from error
    if len(bits) != size or (count % 8 and bits[-1] >> (count % 8)):
        raise ValueError("选区大小与当前候选不一致，请重新打开影像阶段。")
    return tuple(f"region_{ordinal + 1:05d}" for ordinal in range(count) if bits[ordinal >> 3] & (1 << (ordinal & 7)))


def _confirmed_bits(ids: Iterable[str], count: int) -> str:
    bits = bytearray((count + 7) // 8)
    for region_id in ids:
        suffix = region_id.removeprefix("region_")
        ordinal = int(suffix) if 5 <= len(suffix) <= 8 and suffix.isascii() and suffix.isdecimal() else 0
        if not 1 <= ordinal <= count or region_id != f"region_{ordinal:05d}":
            raise ValueError("已确认范围与候选索引不一致。")
        offset = ordinal - 1
        bits[offset >> 3] |= 1 << (offset & 7)
    return base64.b64encode(bits).decode("ascii")


def read_imagery_candidate(store: ProjectQueryStore, job_id: str, page: int = 1, *, compact: bool = False) -> ImageryCandidate:
    outputs = _imagery_outputs(store, job_id)
    manifest = _document(store, outputs["source_manifest"])
    review = manifest.get("review")
    page_count = review["page_count"] if review else 1
    if not 1 <= page <= page_count:
        raise ValueError("候选批次不存在。")
    geometry = _document(store, outputs[f"imagery_page_{page}"] if review else outputs["regions"])
    if any(row.get("pixel_count") is None for row in geometry["regions"]) and "geometry_metrics" in outputs:
        a, b, _, d, e, _ = manifest["imagery"]["transform"]
        pixel_area = abs(a * e - b * d)
        if math.isfinite(pixel_area) and pixel_area > 0:
            counts = {}
            for item in _document(store, outputs["geometry_metrics"])["regions"]:
                pixels = item["footprint_area_xy"] / pixel_area
                if math.isfinite(pixels) and pixels > 0:
                    counts[item["id"]] = round(pixels)
            for row in geometry["regions"]:
                if row.get("pixel_count") is None:
                    row["pixel_count"] = counts.get(row["id"])
    preview = overview = None
    index = None
    encoding = confirmed_bits = confirmation = None
    confirmed_id = confirmed_ids = locked = None
    confirmed_refinement = None
    width, height = manifest["preview"]["width"], manifest["preview"]["height"]
    if page == 1:
        pending = store.list_jobs(command_types=(REGION_SOURCE_CONFIRM_COMMAND,), statuses=REGION_SOURCE_CONFIRM_ACTIVE, limit=1).items
        latest = latest_imagery_job(store) if pending else None
        if latest is not None and latest.job_id == job_id:
            confirmation = pending[0].job_id
        preview = _preview_png(store, outputs["imagery_preview"], width, height)
        if "imagery_overview" in outputs and "imagery_review_index" in outputs:
            overview = _preview_png(store, outputs["imagery_overview"], width, height)
            if compact and review and review.get("index_encoding") == IMAGERY_REVIEW_INDEX_ENCODING:
                _packed_index(store, outputs, review)
                encoding = IMAGERY_REVIEW_INDEX_ENCODING
            else:
                index = tuple(ImageryReviewItem(**item) for item in _document(store, outputs["imagery_review_index"])["items"])
        current_id = store.project_workflow_state().region_set_id
        if current_id is not None:
            current = store.get_region_set(current_id)
            scene = store.get_scene_revision(current.scene_revision_id)
            if scene_imagery_job_id(store, scene) == job_id:
                confirmed_id = current_id
                original_ids = None
                confirmation_id = current.producer_job_id
                if confirmation_id == job_id:
                    confirmation_id = (_document(store, scene.scene_manifest_artifact_id).get("facts") or {}).get("region_confirmation_job_id")
                if confirmation_id:
                    command = store.get_job(confirmation_id).command
                    if command.command_type == REGION_SOURCE_CONFIRM_COMMAND and command.command_version == "2":
                        saved = _document(store, command.artifact_id)
                        original_ids = tuple(saved["included_region_ids"])
                        confirmed_refinement = saved["refinement"]
                if encoding:
                    if original_ids is not None:
                        confirmed_bits = _confirmed_bits(original_ids, review["region_count"])
                    else:
                        with closing(store.iter_region_ids(current_id)) as ids:
                            confirmed_bits = _confirmed_bits(ids, review["region_count"])
                else:
                    confirmed_ids = original_ids if original_ids is not None else tuple(store.iter_region_ids(current_id))
                locked = region_source_revision_lock(store)
    bounds = manifest["imagery"]["bounds"]
    recognition = manifest.get("recognition", {})
    return ImageryCandidate(
        (bounds[0], bounds[1], 0.0, bounds[2], bounds[3], 0.0),
        tuple(manifest["preview"]["transform"]),
        preview,
        tuple(ImageryCandidateRegion(row["id"], row["label"], tuple(row["bbox"]),
              tuple(tuple(tuple(vertex) for vertex in ring) for ring in row["footprint_boundary_xyz"]),
              row.get("pixel_count"), row.get("preview_mode", "exact"))
              for row in geometry["regions"]),
        page, page_count, review["region_count"] if review else len(geometry["regions"]),
        recognition.get("min_region_pixels", 0), recognition.get("filtered_regions", 0),
        width, height, overview, index, _source_grid(manifest["imagery"]) if page == 1 else None,
        confirmed_id, confirmed_ids, locked, encoding, confirmed_bits, confirmation,
        refinement_available=page == 1 and "vegetation_mask" in outputs,
        confirmed_refinement=confirmed_refinement,
    )


def _preview_png(store: ProjectQueryStore, artifact_id: str, width: int, height: int) -> str:
    with store.open_artifact(artifact_id) as stream:
        png = stream.read(9_000_001)
    if (not 24 <= len(png) <= 9_000_000 or not png.startswith(b"\x89PNG\r\n\x1a\n")
            or png[12:16] != b"IHDR" or struct.unpack(">II", png[16:24]) != (width, height)
            or not (1 <= width <= 1600 and 1 <= height <= 1600)):
        raise ValueError("影像预览文件无效。")
    return base64.b64encode(png).decode("ascii")


def resolve_plant_workflow(host: CoreHost, *, scene_revision_id: str, target_type: str) -> ResolvedPlantWorkflow:
    store = host.store
    scene = store.get_scene_revision(scene_revision_id)
    if scene.importer_id != IMAGERY_REGION_PROVIDER_ID:
        return host.workflows.resolve(store, scene_revision_id=scene_revision_id, target_type=target_type)

    manifest = _document(store, scene.scene_manifest_artifact_id)
    source_manifest = _document(store, manifest["facts"]["region_source_manifest_artifact_id"])
    elevation_member_id = source_manifest.get("elevation_member_id")
    if source_manifest.get("recipe_id") != IMAGERY_REGION_RECIPE_ID or not elevation_member_id:
        raise ValueError("影像项目未记录高程绑定，不能输出种植结果。")
    member = next((member for member in store.list_source_assembly_members(scene.source_id)
                   if member.source_member_id == elevation_member_id), None)
    if member is None:
        raise ValueError("影像项目记录的高程文件已不可用。")
    source = store.get_source(member.source_id)
    if source.kind is not SourceKind.GEOTIFF:
        raise ValueError("影像项目绑定的高程不是 GeoTIFF。")
    return host.workflows.resolve(store, scene_revision_id=scene_revision_id, target_type=target_type,
        recipe_id=IMAGERY_REGION_RECIPE_ID,
        terrain_source=SamplingSourceRef("source", source.source_id, source.artifact_id, source.content_hash,
            scene.coordinate_frame_id, assembly_source_id=scene.source_id, source_member_id=member.source_member_id))


def _document(store: ProjectStore | ProjectQueryStore, artifact_id: str) -> dict[str, Any]:
    with store.open_artifact(artifact_id) as stream:
        document = json.load(stream)
    if not isinstance(document, dict):
        raise ValueError("影像任务产物不完整。")
    return document
