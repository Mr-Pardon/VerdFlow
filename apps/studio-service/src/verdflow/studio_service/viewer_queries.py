"""Bounded product projections for Region and Point viewers."""

from __future__ import annotations

from collections import Counter, OrderedDict, defaultdict
from contextlib import AbstractContextManager, closing, nullcontext, suppress
from dataclasses import dataclass
import errno
import json
from itertools import chain
import math
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Callable, Generic, Iterator, TypeVar
from uuid import uuid4

from verdflow.contracts import (
    AssetAssignmentSetInfo,
    PointSetInfo,
    RegionSetInfo,
    RegionSummary,
)
from verdflow.project_store import ProjectQueryStore
from verdflow.planting import AssignmentSetReader, PlantingDocumentError, paired_planting_rows

from .models import (
    Bounds3D,
    PointLegendItem,
    PointReviewItem,
    PointReviewManifest,
    PointViewportChunk,
    RegionClassificationSummary,
    RegionOverview,
    RegionSelectionPage,
    RegionViewerItem,
    RegionViewerManifest,
    RegionViewportChunk,
)
from .point_review_index import DiskPointReviewIndex, point_review_page
from .region_review_index import DiskRegionReviewIndex, RegionIndexError
from .region_overview import RegionOverviewBuilder

if TYPE_CHECKING:
    from shapely.geometry.base import BaseGeometry


CacheValue = TypeVar("CacheValue")
_POINT_INDEX_MEMORY_ROWS = 100_000
_POINT_INDEX_MEMORY_BYTES = 32 * 1024**2
_POINT_INDEX_CACHE_BYTES = 768 * 1024**2
_POINT_PAGE_MAX_ROWS = 5_000
_REGION_INDEX_MEMORY_BYTES = 32 * 1024**2
_REGION_ITEM_VERTICES = 20_000
_REGION_PAGE_VERTICES = 60_000
_REGION_PAGE_BYTES = 8 * 1024**2


class ViewerProjectionError(RuntimeError):
    """A committed viewer Artifact does not satisfy its stored contract."""


class _LruCache(Generic[CacheValue]):
    def __init__(
        self,
        capacity: int,
        *,
        max_weight: int | None = None,
        weight: Callable[[CacheValue], int] | None = None,
        dispose: Callable[[CacheValue], None] | None = None,
    ) -> None:
        self._capacity = capacity
        self._max_weight = max_weight
        self._weight = weight or (lambda _value: 1)
        self._dispose = dispose or (lambda _value: None)
        self._total_weight = 0
        self._values: OrderedDict[str, CacheValue] = OrderedDict()

    def get(self, key: str) -> CacheValue | None:
        value = self._values.get(key)
        if value is not None:
            self._values.move_to_end(key)
        return value

    def put(self, key: str, value: CacheValue) -> CacheValue:
        previous = self._values.pop(key, None)
        if previous is not None:
            self._total_weight -= self._weight(previous)
            if previous is not value:
                self._dispose(previous)
        self._values[key] = value
        self._total_weight += self._weight(value)
        self._values.move_to_end(key)
        # Keep one oversized projection so paging it does not repeatedly parse it.
        while len(self._values) > 1 and (
            len(self._values) > self._capacity
            or (self._max_weight is not None and self._total_weight > self._max_weight)
        ):
            _, evicted = self._values.popitem(last=False)
            self._total_weight -= self._weight(evicted)
            self._dispose(evicted)
        return value

    def clear(self) -> None:
        for value in self._values.values():
            self._dispose(value)
        self._values.clear()
        self._total_weight = 0


@dataclass(frozen=True, slots=True)
class _IndexedPoint:
    ordinal: int
    point_id: str
    region_id: str
    role_key: str
    position: tuple[float, float, float]
    group_id: str | None


@dataclass(frozen=True, slots=True)
class _PointIndex:
    points: tuple[_IndexedPoint, ...]
    bounds: Bounds3D | None
    cell_width: float
    cell_height: float
    columns: int
    rows: int
    buckets: dict[tuple[int, int], tuple[int, ...]]

    def ordinals_in_viewport(
        self,
        *,
        min_x: float,
        min_y: float,
        max_x: float,
        max_y: float,
    ) -> tuple[int, ...]:
        if self.bounds is None or not _intersects(
            self.bounds,
            min_x=min_x,
            min_y=min_y,
            max_x=max_x,
            max_y=max_y,
        ):
            return ()
        first_x = self._column(min_x)
        last_x = self._column(max_x)
        first_y = self._row(min_y)
        last_y = self._row(max_y)
        ordinals: list[int] = []
        for row in range(first_y, last_y + 1):
            for column in range(first_x, last_x + 1):
                ordinals.extend(self.buckets.get((column, row), ()))
        return tuple(sorted(ordinals))

    def _column(self, x: float) -> int:
        assert self.bounds is not None
        raw = int(math.floor((x - self.bounds.min_x) / self.cell_width))
        return min(max(raw, 0), self.columns - 1)

    def _row(self, y: float) -> int:
        assert self.bounds is not None
        raw = int(math.floor((y - self.bounds.min_y) / self.cell_height))
        return min(max(raw, 0), self.rows - 1)


@dataclass(frozen=True, slots=True)
class _Assignment:
    asset_id: str
    target_scale_xyz: tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class _AssetPresentation:
    display_name: str
    reference_radius_m: float | None
    reference_height_m: float | None


class ProjectViewerQueries:
    """Build UI-safe projections without exposing Artifact paths or documents."""

    def __init__(
        self, store: ProjectQueryStore, *, cache_root: Path | None = None,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> None:
        self._store = store
        self._cache_root = cache_root
        self._cancelled = cancelled
        self._temporary: TemporaryDirectory[str] | None = None
        self._region_temporary: TemporaryDirectory[str] | None = None
        self._region_summaries = _LruCache[tuple[RegionSummary, ...]](4)
        self._region_overviews = _LruCache[RegionOverview](2)
        self._region_geometry = _LruCache[dict[str, object]](2)
        self._region_items = _LruCache[RegionViewerItem](
            2048, max_weight=_REGION_PAGE_BYTES * 2, weight=_region_item_weight,
        )
        self._region_polygons: _LruCache[tuple[BaseGeometry, int]] = _LruCache(
            128, max_weight=1_000_000, weight=lambda entry: entry[1],
        )
        self._region_indexes = _LruCache[DiskRegionReviewIndex](
            4, max_weight=512 * 1024**2, weight=lambda index: index.byte_size,
            dispose=DiskRegionReviewIndex.close,
        )
        self._point_indexes = _LruCache[_PointIndex](
            8, max_weight=250_000, weight=lambda index: len(index.points),
        )
        self._assignment_maps = _LruCache[dict[str, _Assignment]](
            8, max_weight=250_000, weight=len,
        )
        self._scale_maxima = _LruCache[dict[str, float]](16)
        self._disk_indexes = _LruCache[DiskPointReviewIndex](
            8, max_weight=_POINT_INDEX_CACHE_BYTES, weight=lambda index: index.byte_size,
            dispose=DiskPointReviewIndex.close,
        )
        self._asset_presentations = _LruCache[dict[str, _AssetPresentation]](4)

    def close(self) -> None:
        self._region_indexes.clear()
        self._region_geometry.clear()
        self._region_items.clear()
        self._region_polygons.clear()
        self._region_summaries.clear()
        self._region_overviews.clear()
        self._disk_indexes.clear()
        self._point_indexes.clear()
        self._assignment_maps.clear()
        self._scale_maxima.clear()
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None
        if self._region_temporary is not None:
            self._region_temporary.cleanup()
            self._region_temporary = None

    def region_manifest(self, region_set_id: str) -> RegionViewerManifest:
        region_set = self._store.get_region_set(region_set_id)
        regions = self._regions(region_set_id)
        counts: Counter[str | None] = Counter()
        areas: defaultdict[str | None, float] = defaultdict(float)
        for region in regions:
            counts[region.classification] += 1
            areas[region.classification] += region.footprint_area_xy or 0.0
        classifications = tuple(
            RegionClassificationSummary(
                classification=classification,
                region_count=counts[classification],
                footprint_area_xy=areas[classification],
            )
            for classification in sorted(
                counts,
                key=lambda value: (value is None, value or ""),
            )
        )
        return RegionViewerManifest(
            region_set_id=region_set.region_set_id,
            scene_revision_id=region_set.scene_revision_id,
            coordinate_frame_id=region_set.coordinate_frame_id,
            region_count=region_set.region_count,
            bounds=_region_bounds(regions),
            classifications=classifications,
        )

    def region_chunk(
        self,
        region_set_id: str,
        *,
        min_x: float,
        min_y: float,
        max_x: float,
        max_y: float,
        classifications: tuple[str, ...] = (),
        after_ordinal: int = -1,
        limit: int = 1000,
        simplification_tolerance: float = 0.0,
        overview_threshold: int = 0,
    ) -> RegionViewportChunk:
        _validate_viewport(min_x, min_y, max_x, max_y)
        _validate_page(after_ordinal, limit)
        _validate_simplification_tolerance(simplification_tolerance)
        region_set = self._store.get_region_set(region_set_id)
        allowed = set(classifications)
        matches = (
            region
            for region in self._regions(region_set_id)
            if region.ordinal > after_ordinal
            and (not allowed or region.classification in allowed)
            and _intersects(
                _bounds_from_region(region),
                min_x=min_x,
                min_y=min_y,
                max_x=max_x,
                max_y=max_y,
            )
        )
        page = []
        for count, region in enumerate(matches, 1):
            if count <= limit + 1:
                page.append(region)
            if overview_threshold and after_ordinal == -1:
                if count > overview_threshold:
                    return RegionViewportChunk((), None, True, overview_recommended=True)
            elif count > limit:
                break
        truncated = len(page) > limit
        if truncated:
            page.pop()
        if not page:
            return RegionViewportChunk((), None, False)

        artifact_id = region_set.geometry_artifact_id
        tolerance = _lod_tolerance(simplification_tolerance)
        items_list: list[RegionViewerItem] = []
        remaining_vertices = _REGION_PAGE_VERTICES
        remaining_bytes = _REGION_PAGE_BYTES
        with self._region_lookup(region_set) as geometry:
            for region in page:
                if self._cancelled():
                    raise ViewerProjectionError("Region review was closed")
                key = f"{artifact_id}:{tolerance:.12g}:{region.region_id}"
                item = self._region_items.get(key)
                if item is None:
                    raw = geometry(region.region_id)
                    rings = raw.get("footprint_boundary_xyz") if isinstance(raw, dict) else None
                    if (overview_threshold and isinstance(rings, list) and all(isinstance(ring, list) for ring in rings)
                            and sum(map(len, rings)) > _REGION_ITEM_VERTICES):
                        return RegionViewportChunk((), None, True, overview_recommended=True)
                    item = _region_item(
                        region, raw, simplification_tolerance=tolerance,
                    )
                    self._region_items.put(key, item)
                if overview_threshold and item.boundary_mode != "detail":
                    return RegionViewportChunk((), None, True, overview_recommended=True)
                vertices = _region_vertices(item)
                weight = _region_item_weight(item)
                if vertices > remaining_vertices or weight > remaining_bytes:
                    if not items_list:
                        raise ViewerProjectionError("Region metadata exceeds the preview response budget")
                    truncated = True
                    break
                items_list.append(item)
                remaining_vertices -= vertices
                remaining_bytes -= weight
        items = tuple(items_list)
        return RegionViewportChunk(
            items=items,
            next_after_ordinal=(items[-1].ordinal if truncated and items else None),
            truncated=truncated,
        )

    def region_overview(
        self, region_set_id: str, *, min_x: float, min_y: float, max_x: float, max_y: float,
        classifications: tuple[str, ...] = (),
    ) -> RegionOverview:
        _validate_viewport(min_x, min_y, max_x, max_y)
        if not all(1e-12 <= span < float("inf") for span in (max_x - min_x, max_y - min_y)):
            raise ValueError("Region overview requires a finite non-empty viewport")
        region_set = self._store.get_region_set(region_set_id)
        summaries = self._regions(region_set_id)
        allowed = set(classifications)
        bounds = Bounds3D(min_x, min_y, 0.0, max_x, max_y, 0.0)
        key = repr((region_set_id, bounds, sorted(allowed)))
        cached = self._region_overviews.get(key)
        if cached is not None:
            return cached
        matches = (
            (index, region.region_id) for index, region in enumerate(summaries)
            if (not allowed or region.classification in allowed)
            and region.max_x >= min_x and region.min_x <= max_x
            and region.max_y >= min_y and region.min_y <= max_y
        )
        builder = RegionOverviewBuilder(bounds, cancelled=self._cancelled)
        first = next(matches, None)
        if first is not None:
            pairs = chain((first,), matches)
            artifact_id = region_set.geometry_artifact_id
            existing_index = self._region_indexes.get(artifact_id)
            if (
                (existing_index is None or not existing_index.path.is_file())
                and self._store.get_artifact(artifact_id).byte_size > _REGION_INDEX_MEMORY_BYTES
            ):
                by_id = {region_id: index for index, region_id in pairs}
                last_index, seen, in_order = -1, 0, True

                def project_boundary(region_id: str, rings: list[object]) -> None:
                    nonlocal last_index, seen, in_order
                    index = by_id.get(region_id)
                    if index is None or not in_order:
                        return
                    if index <= last_index:
                        in_order = False
                        return
                    self._paint_region_overview(builder, artifact_id, region_id, index, lambda _: {"footprint_boundary_xyz": rings})
                    last_index = index
                    seen += 1

                # Project from the initial index scan, provided Artifact and summary order agree.
                self._disk_region_index(artifact_id, region_set.region_count, on_boundary=project_boundary)
                if in_order and seen == len(by_id):
                    return self._region_overviews.put(key, builder.finish(summaries))
                builder = RegionOverviewBuilder(bounds, cancelled=self._cancelled)
                pairs = ((index, region_id) for region_id, index in by_id.items())
            with self._region_lookup(region_set) as geometry:
                for index, region_id in pairs:
                    if self._cancelled():
                        raise ViewerProjectionError("Region review was closed")
                    self._paint_region_overview(builder, artifact_id, region_id, index, geometry)
        return self._region_overviews.put(key, builder.finish(summaries))

    def _region_polygon(self, artifact_id: str, region_id: str, geometry, *, complex_only: bool = False):
        from shapely import prepare
        from shapely.geometry import Polygon

        key = f"{artifact_id}:{region_id}"
        cached = self._region_polygons.get(key)
        if cached is not None:
            polygon, vertices = cached
            if not complex_only or vertices > _REGION_ITEM_VERTICES:
                return polygon, None
        rings = _exact_region_boundary(geometry(region_id))
        vertices = sum(map(len, rings))
        if (complex_only and vertices <= _REGION_ITEM_VERTICES) or not rings or any(len(ring) < 3 for ring in rings):
            return None, rings
        polygon = Polygon(rings[0], rings[1:])
        if polygon.is_empty or not polygon.is_valid:
            return None, rings
        prepare(polygon)
        self._region_polygons.put(key, (polygon, vertices))
        return polygon, None

    def _paint_region_overview(self, builder, artifact_id, region_id, index, geometry) -> None:
        polygon, rings = self._region_polygon(artifact_id, region_id, geometry, complex_only=True)
        if polygon is not None:
            builder.add_polygon(index, polygon)
        else:
            builder.add(index, rings)

    def region_selection(
        self, region_set_id: str, *, corners: tuple[tuple[float, float], ...],
        classifications: tuple[str, ...] = (), after_ordinal: int = -1,
    ) -> RegionSelectionPage:
        """Hit test original geometry, including Regions omitted from display pages."""
        from shapely.geometry import MultiPoint, Point

        _validate_page(after_ordinal, 5000)
        if len(corners) not in {1, 4} or not all(
            len(xy) == 2 and all(math.isfinite(n) for n in xy) for xy in corners
        ):
            raise ValueError("Selection requires a point or four finite corners")
        point_only = len(corners) == 1
        probe = Point(corners[0]) if point_only else MultiPoint(corners).convex_hull
        min_x, min_y, max_x, max_y = probe.bounds
        region_set = self._store.get_region_set(region_set_id)
        composition = self._store.current_planting_composition()
        occupied = (
            {region_id for batch in self._store.list_planting_batches() for region_id in batch.selected_region_ids}
            if composition is not None and composition.region_set_id == region_set_id else set()
        )
        allowed = set(classifications)
        summaries = self._regions(region_set_id)
        selected: list[str] = []
        next_after = None
        remaining = _REGION_PAGE_BYTES
        with self._region_lookup(region_set) as geometry:
            for region in (reversed(summaries) if point_only else iter(summaries)):
                if self._cancelled():
                    raise ViewerProjectionError("Region review was closed")
                if (
                    region.ordinal <= after_ordinal or region.region_id in occupied
                    or (allowed and region.classification not in allowed)
                    or not _intersects(_bounds_from_region(region), min_x=min_x, min_y=min_y, max_x=max_x, max_y=max_y)
                ):
                    continue
                polygon, rings = self._region_polygon(region_set.geometry_artifact_id, region.region_id, geometry)
                if polygon is None:
                    hit = _exact_region_hit(rings, probe, point_only)
                elif not point_only:
                    hit = polygon.intersects(probe)
                else:
                    hit = polygon.contains(probe)
                    if not hit and polygon.intersects(probe):
                        hit = _exact_region_hit(_exact_region_boundary(geometry(region.region_id)), probe, point_only)
                if not hit:
                    continue
                weight = 16 + 6 * len(region.region_id)
                if len(selected) == 5000 or weight > remaining:
                    if not selected:
                        raise ViewerProjectionError("Region ID exceeds the selection response budget")
                    return RegionSelectionPage(tuple(selected), next_after)
                selected.append(region.region_id)
                if point_only:
                    break
                remaining -= weight
                next_after = region.ordinal
        return RegionSelectionPage(tuple(selected), None)

    def point_manifest(
        self,
        point_set_id: str,
        assignment_set_id: str,
    ) -> PointReviewManifest:
        point_set = self._store.get_point_set(point_set_id)
        assignment = self._store.get_assignment_set(assignment_set_id)
        _validate_assignment_lineage(point_set, assignment)
        planting_run = self._store.get_planting_run(point_set.planting_run_id)
        asset_presentations = self._asset_presentations_for(assignment)
        asset_summaries = self._store.list_assignment_assets(assignment_set_id)
        max_scale_by_asset = self._max_scales(
            assignment, {item.asset_id for item in asset_summaries},
            stream=self._uses_disk_index(point_set, assignment),
        )
        asset_legends = tuple(
            _asset_legend(
                item.asset_id,
                item.point_count,
                asset_presentations.get(item.asset_id),
                max_scale_by_asset.get(item.asset_id, 0.0),
            )
            for item in asset_summaries
        )
        return PointReviewManifest(
            point_set_id=point_set.point_set_id,
            assignment_set_id=assignment.asset_assignment_set_id,
            planting_run_id=point_set.planting_run_id,
            region_set_id=planting_run.region_set_id,
            coordinate_frame_id=point_set.coordinate_frame_id,
            kind=point_set.kind.value,
            target_type=assignment.target_type,
            point_count=point_set.point_count,
            bounds=_point_set_bounds(point_set),
            roles=tuple(
                PointLegendItem(item.role_key, item.point_count)
                for item in self._store.list_point_set_roles(point_set_id)
            ),
            assets=asset_legends,
            max_resolved_radius_m=max(
                (
                    item.max_resolved_radius_m or 0.0
                    for item in asset_legends
                ),
                default=0.0,
            ),
        )

    def _asset_presentations_for(
        self,
        assignment: AssetAssignmentSetInfo,
    ) -> dict[str, _AssetPresentation]:
        binding = self._store.get_effective_binding_snapshot(
            assignment.effective_binding_snapshot_id
        )
        asset_use = self._store.get_target_asset_use_snapshot(
            binding.target_asset_use_snapshot_id
        )
        cached = self._asset_presentations.get(asset_use.snapshot_artifact_id)
        if cached is not None:
            return cached
        document = self._read_document(asset_use.snapshot_artifact_id)
        if (
            document.get("schema")
            != "verdflow.target-asset-use-snapshot"
            or str(document.get("version")) != "1"
        ):
            raise ViewerProjectionError(
                "TargetAssetUseSnapshot schema is unsupported"
            )
        rows = document.get("assets")
        if not isinstance(rows, list):
            raise ViewerProjectionError(
                "TargetAssetUseSnapshot assets are required"
            )
        presentations: dict[str, _AssetPresentation] = {}
        for row in rows:
            if not isinstance(row, dict):
                raise ViewerProjectionError(
                    "TargetAssetUseSnapshot asset must be an object"
                )
            asset_id = _required_text(
                row,
                "asset_id",
                "TargetAssetUseSnapshot asset",
            )
            display_name = _required_text(
                row,
                "display_name",
                "TargetAssetUseSnapshot asset",
            )
            if asset_id in presentations:
                raise ViewerProjectionError(
                    f"TargetAssetUseSnapshot asset is duplicated: {asset_id}"
                )
            presentations[asset_id] = _AssetPresentation(
                display_name=display_name,
                reference_radius_m=_optional_positive_number(
                    row.get("reference_radius_m"),
                    "TargetAssetUseSnapshot asset reference_radius_m",
                ),
                reference_height_m=_optional_positive_number(
                    row.get("reference_height_m"),
                    "TargetAssetUseSnapshot asset reference_height_m",
                ),
            )
        return self._asset_presentations.put(
            asset_use.snapshot_artifact_id,
            presentations,
        )

    def point_chunk(
        self,
        point_set_id: str,
        assignment_set_id: str,
        *,
        min_x: float,
        min_y: float,
        max_x: float,
        max_y: float,
        roles: tuple[str, ...] = (),
        assets: tuple[str, ...] = (),
        after_ordinal: int = -1,
        limit: int = 5000,
    ) -> PointViewportChunk:
        _validate_viewport(min_x, min_y, max_x, max_y)
        _validate_page(after_ordinal, limit)
        if limit > _POINT_PAGE_MAX_ROWS:
            raise ValueError(f"Point preview limit must not exceed {_POINT_PAGE_MAX_ROWS}")
        point_set = self._store.get_point_set(point_set_id)
        assignment_set = self._store.get_assignment_set(assignment_set_id)
        _validate_assignment_lineage(point_set, assignment_set)
        stored_bounds = _point_set_bounds(point_set)
        if point_set.point_count == 0 or (
            stored_bounds is not None
            and not _intersects(
                stored_bounds,
                min_x=min_x,
                min_y=min_y,
                max_x=max_x,
                max_y=max_y,
            )
        ):
            return PointViewportChunk(items=(), next_after_ordinal=None, truncated=False)
        if self._uses_disk_index(point_set, assignment_set):
            try:
                return self._disk_index(point_set, assignment_set).chunk(
                    (min_x, min_y, max_x, max_y),
                    roles=roles, assets=assets, after_ordinal=after_ordinal, limit=limit,
                )
            except sqlite3.Error as error:
                raise ViewerProjectionError("Unable to query the point review cache") from error
        index = self._point_index(point_set.point_artifact_id)
        assignments = self._assignments(
            assignment_set.assignment_artifact_id
        )
        if len(index.points) != point_set.point_count:
            raise ViewerProjectionError("PointSet point count is inconsistent")
        if len(assignments) != assignment_set.point_count:
            raise ViewerProjectionError(
                "AssignmentSet point count is inconsistent"
            )
        allowed_roles = set(roles)
        allowed_assets = set(assets)
        def visible() -> Iterator[PointReviewItem]:
            for ordinal in index.ordinals_in_viewport(
                min_x=min_x, min_y=min_y, max_x=max_x, max_y=max_y,
            ):
                if ordinal <= after_ordinal:
                    continue
                point = index.points[ordinal]
                x, y, _ = point.position
                if not (min_x <= x <= max_x and min_y <= y <= max_y):
                    continue
                if allowed_roles and point.role_key not in allowed_roles:
                    continue
                assignment = assignments.get(point.point_id)
                if assignment is None:
                    raise ViewerProjectionError(f"Assignment is missing for point: {point.point_id}")
                if not allowed_assets or assignment.asset_id in allowed_assets:
                    yield _review_item(point, assignment)

        return point_review_page(visible(), limit=limit)

    def _uses_disk_index(self, point: PointSetInfo, assignment: AssetAssignmentSetInfo) -> bool:
        if point.point_count > _POINT_INDEX_MEMORY_ROWS:
            return True
        size = self._store.get_artifact(point.point_artifact_id).byte_size
        size += self._store.get_artifact(assignment.assignment_artifact_id).byte_size
        return size > _POINT_INDEX_MEMORY_BYTES

    def _max_scales(
        self, assignment: AssetAssignmentSetInfo, allowed_assets: set[str], *, stream: bool,
    ) -> dict[str, float]:
        artifact = assignment.assignment_artifact_id
        cached = self._scale_maxima.get(artifact)
        if cached is not None:
            return cached
        maxima: defaultdict[str, float] = defaultdict(float)
        try:
            if stream:
                with self._store.open_artifact(artifact) as source:
                    reader = AssignmentSetReader(source, expected_point_count=assignment.point_count)
                    for ordinal, row in enumerate(reader.rows()):
                        if ordinal % 2048 == 0:
                            self._check_cancelled()
                        item = _assignment_item(row)
                        if item.asset_id not in allowed_assets:
                            raise ViewerProjectionError("Assignment asset differs from its Core summary")
                        x, y, _ = item.target_scale_xyz
                        maxima[item.asset_id] = max(maxima[item.asset_id], abs(x), abs(y))
                    header = reader.header
                    if (
                        header["schema"] != "verdflow.asset-assignment-set"
                        or header["version"] != "1"
                        or header["semantic_hash"] != assignment.semantic_hash
                        or header["target_type"] != assignment.target_type
                    ):
                        raise ViewerProjectionError("AssignmentSet header differs from its Core record")
            else:
                for item in self._assignments(artifact).values():
                    x, y, _ = item.target_scale_xyz
                    maxima[item.asset_id] = max(maxima[item.asset_id], abs(x), abs(y))
        except PlantingDocumentError as error:
            raise ViewerProjectionError(str(error)) from error
        return self._scale_maxima.put(artifact, dict(maxima))

    def _disk_index(self, point: PointSetInfo, assignment: AssetAssignmentSetInfo) -> DiskPointReviewIndex:
        key = f"{point.point_artifact_id}:{assignment.assignment_artifact_id}"
        cached = self._disk_indexes.get(key)
        if cached is not None and cached.path.is_file():
            return cached
        if self._cache_root is None:
            raise ViewerProjectionError("Large point reviews require a project cache directory")
        path: Path | None = None
        try:
            if self._temporary is None:
                self._cache_root.mkdir(exist_ok=True)
                self._temporary = TemporaryDirectory(
                    prefix="point-review-", dir=self._cache_root, ignore_cleanup_errors=True,
                )
            directory = Path(self._temporary.name).resolve()
            path = directory / f"{uuid4().hex}.sqlite"
            with closing(self._paired_items(point, assignment, directory)) as items:
                index = DiskPointReviewIndex.build(
                    path, items, bounds=_point_set_bounds(point), cancelled=self._cancelled,
                )
            return self._disk_indexes.put(key, index)
        except (OSError, sqlite3.Error, PlantingDocumentError) as error:
            if path is not None:
                with suppress(OSError):
                    path.unlink(missing_ok=True)
            if self._cancelled():
                raise ViewerProjectionError("Point review was closed") from error
            if (
                isinstance(error, OSError) and error.errno == errno.ENOSPC
                or getattr(error, "sqlite_errorcode", None) == sqlite3.SQLITE_FULL
            ):
                raise ViewerProjectionError("Point review cache has insufficient disk space") from error
            if isinstance(error, PlantingDocumentError):
                raise ViewerProjectionError(str(error)) from error
            raise ViewerProjectionError("Unable to build the point review cache") from error
        except BaseException:
            if path is not None:
                with suppress(OSError):
                    path.unlink(missing_ok=True)
            raise

    def _paired_items(
        self, point: PointSetInfo, assignment: AssetAssignmentSetInfo, scratch: Path,
    ) -> Iterator[PointReviewItem]:
        with (
            self._store.open_artifact(point.point_artifact_id) as points_file,
            self._store.open_artifact(assignment.assignment_artifact_id) as assignments_file,
            closing(paired_planting_rows(
                points_file, assignments_file, point_count=point.point_count,
                point_kind=point.kind.value, assignment_hash=assignment.semantic_hash,
                target_type=assignment.target_type, scratch_root=scratch,
                on_rows=self._check_cancelled,
            )) as pairs,
        ):
            for ordinal, (row, mapping) in enumerate(pairs):
                yield _review_item(_indexed_point(ordinal, row), _assignment_item(mapping))

    def _check_cancelled(self) -> None:
        if self._cancelled():
            raise ViewerProjectionError("Point review was closed")

    def _regions(self, region_set_id: str) -> tuple[RegionSummary, ...]:
        cached = self._region_summaries.get(region_set_id)
        if cached is not None:
            return cached
        return self._region_summaries.put(
            region_set_id,
            self._store.list_regions(region_set_id),
        )

    def _geometry_by_id(self, artifact_id: str) -> dict[str, object]:
        cached = self._region_geometry.get(artifact_id)
        if cached is not None:
            return cached
        document = self._read_document(artifact_id)
        if (
            document.get("schema") != "verdflow.regions"
            or str(document.get("version")) != "1"
        ):
            raise ViewerProjectionError("Region geometry schema is unsupported")
        rows = document.get("regions")
        if not isinstance(rows, list):
            raise ViewerProjectionError("Region geometry rows are required")
        result: dict[str, object] = {}
        for row in rows:
            if not isinstance(row, dict):
                raise ViewerProjectionError("Region geometry row must be an object")
            region_id = row.get("id")
            if not isinstance(region_id, str) or not region_id:
                raise ViewerProjectionError("Region geometry id is required")
            if region_id in result:
                raise ViewerProjectionError(
                    f"Region geometry is duplicated: {region_id}"
                )
            result[region_id] = row
        return self._region_geometry.put(artifact_id, result)

    def _disk_region_index(
        self, artifact_id: str, count: int, *, on_boundary: Callable[[str, list[object]], None] | None = None,
    ) -> DiskRegionReviewIndex:
        cached = self._region_indexes.get(artifact_id)
        if cached is not None and cached.path.is_file():
            return cached
        if self._cache_root is None:
            raise ViewerProjectionError("Large region reviews require a project cache directory")
        path: Path | None = None
        try:
            if self._region_temporary is None:
                self._cache_root.mkdir(exist_ok=True)
                self._region_temporary = TemporaryDirectory(
                    prefix="region-review-", dir=self._cache_root, ignore_cleanup_errors=True,
                )
            path = Path(self._region_temporary.name).resolve() / f"{uuid4().hex}.sqlite"
            with self._store.open_artifact(artifact_id) as source:
                index = DiskRegionReviewIndex.build(
                    path, source, count=count, cancelled=self._cancelled, on_boundary=on_boundary,
                )
            self._region_indexes.put(artifact_id, index)
            path = None
            return index
        except (OSError, sqlite3.Error, RegionIndexError) as error:
            if self._cancelled():
                raise ViewerProjectionError("Region review was closed") from error
            if (
                isinstance(error, OSError) and error.errno == errno.ENOSPC
                or getattr(error, "sqlite_errorcode", None) == sqlite3.SQLITE_FULL
            ):
                raise ViewerProjectionError("Region review cache has insufficient disk space") from error
            if isinstance(error, RegionIndexError):
                raise ViewerProjectionError(str(error)) from error
            raise ViewerProjectionError("Unable to build the region review cache") from error
        finally:
            if path is not None:
                with suppress(OSError):
                    path.unlink(missing_ok=True)

    def _region_lookup(
        self, region_set: RegionSetInfo,
    ) -> AbstractContextManager[Callable[[str], object]]:
        artifact_id = region_set.geometry_artifact_id
        if self._store.get_artifact(artifact_id).byte_size > _REGION_INDEX_MEMORY_BYTES:
            return self._disk_region_index(artifact_id, region_set.region_count).lookup()
        return nullcontext(self._geometry_by_id(artifact_id).get)

    def _point_index(self, artifact_id: str) -> _PointIndex:
        cached = self._point_indexes.get(artifact_id)
        if cached is not None:
            return cached
        document = self._read_document(artifact_id)
        if (
            document.get("schema") != "verdflow.points"
            or str(document.get("version")) != "2"
        ):
            raise ViewerProjectionError("PointSet schema is unsupported")
        rows = document.get("points")
        if not isinstance(rows, list):
            raise ViewerProjectionError("PointSet points are required")
        points = tuple(_indexed_point(index, row) for index, row in enumerate(rows))
        return self._point_indexes.put(artifact_id, _build_point_index(points))

    def _assignments(self, artifact_id: str) -> dict[str, _Assignment]:
        cached = self._assignment_maps.get(artifact_id)
        if cached is not None:
            return cached
        document = self._read_document(artifact_id)
        if (
            document.get("schema") != "verdflow.asset-assignment-set"
            or str(document.get("version")) != "1"
        ):
            raise ViewerProjectionError("AssignmentSet schema is unsupported")
        rows = document.get("assignments")
        if not isinstance(rows, list):
            raise ViewerProjectionError("AssignmentSet rows are required")
        result: dict[str, _Assignment] = {}
        for row in rows:
            if not isinstance(row, dict):
                raise ViewerProjectionError("Assignment row must be an object")
            point_id = _required_text(row, "point_id", "Assignment")
            if point_id in result:
                raise ViewerProjectionError(
                    f"Assignment is duplicated for point: {point_id}"
                )
            result[point_id] = _assignment_item(row)
        return self._assignment_maps.put(artifact_id, result)

    def _read_document(self, artifact_id: str) -> dict[str, object]:
        try:
            with self._store.open_artifact(artifact_id) as stream:
                value = json.load(stream)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ViewerProjectionError("Viewer Artifact is not valid JSON") from error
        if not isinstance(value, dict):
            raise ViewerProjectionError("Viewer Artifact must be an object")
        return value


def _assignment_item(row: dict[str, object]) -> _Assignment:
    return _Assignment(
        asset_id=_required_text(row, "asset_id", "Assignment"),
        target_scale_xyz=_vector3(row.get("target_scale_xyz"), "Assignment target_scale_xyz"),
    )


def _review_item(point: _IndexedPoint, assignment: _Assignment) -> PointReviewItem:
    return PointReviewItem(
        point.point_id, point.ordinal, point.region_id, point.role_key, point.position,
        assignment.asset_id, assignment.target_scale_xyz, point.group_id,
    )


def _asset_legend(
    asset_id: str,
    point_count: int,
    presentation: _AssetPresentation | None,
    max_scale_xy: float,
) -> PointLegendItem:
    radius = presentation.reference_radius_m if presentation is not None else None
    return PointLegendItem(
        key=asset_id,
        point_count=point_count,
        display_name=(presentation.display_name if presentation is not None else None),
        reference_radius_m=radius,
        reference_height_m=(presentation.reference_height_m if presentation is not None else None),
        max_resolved_radius_m=(
            radius * max_scale_xy if radius is not None else None
        ),
    )


def _optional_positive_number(value: object, label: str) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ViewerProjectionError(f"{label} must be a number")
    result = float(value)
    if not math.isfinite(result) or result <= 0.0:
        raise ViewerProjectionError(f"{label} must be finite and positive")
    return result


def _region_item(
    region: RegionSummary,
    value: object,
    *,
    simplification_tolerance: float = 0.0,
) -> RegionViewerItem:
    boundaries = _exact_region_boundary(value)
    boundary_mode = "detail"
    if sum(map(len, boundaries)) > _REGION_ITEM_VERTICES:
        bounded = _overview_boundary(boundaries, simplification_tolerance)
        boundary_mode = "overview" if bounded else "extent"
    else:
        bounded = tuple(_simplify_closed_ring(ring, simplification_tolerance) for ring in boundaries)
    return RegionViewerItem(
        region_id=region.region_id,
        ordinal=region.ordinal,
        label=region.label,
        classification=region.classification,
        strategy_hint=region.strategy_hint,
        footprint_area_xy=region.footprint_area_xy,
        bounds=_bounds_from_region(region),
        footprint_boundary_xyz=bounded,
        boundary_mode=boundary_mode,
    )


def _exact_region_boundary(value: object) -> list[tuple[tuple[float, float, float], ...]]:
    if not isinstance(value, dict):
        raise ViewerProjectionError("Region geometry is missing")
    loops = value.get("footprint_boundary_xyz", [])
    if not isinstance(loops, list):
        raise ViewerProjectionError("Region boundary is invalid")
    boundaries: list[tuple[tuple[float, float, float], ...]] = []
    for loop in loops:
        if not isinstance(loop, list):
            raise ViewerProjectionError("Region boundary loop is invalid")
        boundary = tuple(
            _vector3(vertex, "Region boundary vertex") for vertex in loop
        )
        boundaries.append(boundary)
    return boundaries


def _exact_region_hit(rings: list[tuple[tuple[float, float, float], ...]], probe: BaseGeometry, point_only: bool) -> bool:
    from shapely.geometry import LinearRing, Polygon

    sample = probe if point_only else probe.representative_point()
    inside = False
    for ring in rings:
        if len(ring) < 3:
            continue
        boundary = LinearRing(ring)
        if not point_only and boundary.intersects(probe):
            return True
        # Match the viewer's even-odd ring semantics, including detached exteriors.
        inside ^= Polygon(boundary).contains(sample)
    return inside


def _region_vertices(item: RegionViewerItem) -> int:
    return sum(map(len, item.footprint_boundary_xyz))


def _region_item_weight(item: RegionViewerItem) -> int:
    return 512 + _region_vertices(item) * 128 + 6 * sum(map(len, (
        item.region_id, item.label, item.classification or "", item.strategy_hint or "",
    )))


def _overview_boundary(
    rings: list[tuple[tuple[float, float, float], ...]], tolerance: float,
) -> tuple[tuple[tuple[float, float, float], ...], ...]:
    from shapely.errors import GEOSException
    from shapely.geometry import Polygon

    if 4 * len(rings) > _REGION_ITEM_VERTICES or any(len(ring) < 3 for ring in rings):
        return ()
    try:
        polygon = Polygon(rings[0], rings[1:])
        if polygon.is_empty or not polygon.is_valid:
            return ()
        x0, y0, x1, y1 = polygon.bounds
        span = max(x1 - x0, y1 - y0)
        if not 0 < span < float("inf"):
            return ()
        step = max(tolerance, span / 16_384)
        for _ in range(8):
            outline = polygon.simplify(step, preserve_topology=True)
            if outline.geom_type == "Polygon" and not outline.is_empty:
                parts = (outline.exterior, *outline.interiors)
                if len(parts) == len(rings) and sum(len(part.coords) for part in parts) <= _REGION_ITEM_VERTICES:
                    return tuple(tuple(part.coords) for part in parts)
            step *= 4
    except (ValueError, GEOSException):
        pass
    return ()


def _lod_tolerance(value: float) -> float:
    if value <= 0.0:
        return 0.0
    return 2.0 ** math.floor(math.log2(value))


def _simplify_closed_ring(
    ring: tuple[tuple[float, float, float], ...],
    tolerance: float,
) -> tuple[tuple[float, float, float], ...]:
    """Reduce display-only XY detail without modifying stored geometry."""
    if tolerance <= 0.0 or len(ring) <= 4:
        return ring
    points = ring[:-1] if ring[0][:2] == ring[-1][:2] else ring
    if len(points) <= 3:
        return ring
    split = max(
        range(1, len(points)),
        key=lambda index: _distance_squared(points[0], points[index]),
    )
    tolerance_squared = tolerance * tolerance
    first = _simplify_open_line(points[: split + 1], tolerance_squared)
    second = _simplify_open_line(
        points[split:] + (points[0],),
        tolerance_squared,
    )
    simplified = first[:-1] + second[:-1]
    return simplified if len(simplified) >= 3 else ring


def _simplify_open_line(
    points: tuple[tuple[float, float, float], ...],
    tolerance_squared: float,
) -> tuple[tuple[float, float, float], ...]:
    if len(points) <= 2:
        return points
    retained = {0, len(points) - 1}
    pending = [(0, len(points) - 1)]
    while pending:
        start, end = pending.pop()
        furthest = -1
        furthest_distance = tolerance_squared
        for index in range(start + 1, end):
            distance = _segment_distance_squared(
                points[index],
                points[start],
                points[end],
            )
            if distance > furthest_distance:
                furthest = index
                furthest_distance = distance
        if furthest >= 0:
            retained.add(furthest)
            pending.append((start, furthest))
            pending.append((furthest, end))
    return tuple(points[index] for index in sorted(retained))


def _segment_distance_squared(
    point: tuple[float, float, float],
    start: tuple[float, float, float],
    end: tuple[float, float, float],
) -> float:
    delta_x = end[0] - start[0]
    delta_y = end[1] - start[1]
    length_squared = delta_x * delta_x + delta_y * delta_y
    if length_squared <= 0.0:
        return _distance_squared(point, start)
    ratio = (
        (point[0] - start[0]) * delta_x
        + (point[1] - start[1]) * delta_y
    ) / length_squared
    ratio = min(1.0, max(0.0, ratio))
    projection_x = start[0] + ratio * delta_x
    projection_y = start[1] + ratio * delta_y
    return (
        (point[0] - projection_x) ** 2
        + (point[1] - projection_y) ** 2
    )


def _distance_squared(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
) -> float:
    return (first[0] - second[0]) ** 2 + (first[1] - second[1]) ** 2


def _indexed_point(ordinal: int, value: object) -> _IndexedPoint:
    if not isinstance(value, dict):
        raise ViewerProjectionError("PointSet point must be an object")
    group_id = value.get("group_id")
    if group_id is not None and (not isinstance(group_id, str) or not group_id):
        raise ViewerProjectionError("PointSet group_id is invalid")
    return _IndexedPoint(
        ordinal=ordinal,
        point_id=_required_text(value, "id", "PointSet point"),
        region_id=_required_text(value, "region_id", "PointSet point"),
        role_key=_required_text(value, "role_key", "PointSet point"),
        position=_vector3(value.get("resolved_position"), "Point position"),
        group_id=group_id,
    )


def _build_point_index(points: tuple[_IndexedPoint, ...]) -> _PointIndex:
    bounds = _point_bounds(points)
    if bounds is None:
        return _PointIndex(points, None, 1.0, 1.0, 1, 1, {})
    target_cells = max(1, math.ceil(len(points) / 256))
    columns = max(1, math.ceil(math.sqrt(target_cells)))
    rows = columns
    cell_width = max((bounds.max_x - bounds.min_x) / columns, 1e-9)
    cell_height = max((bounds.max_y - bounds.min_y) / rows, 1e-9)
    buckets: defaultdict[tuple[int, int], list[int]] = defaultdict(list)
    for point in points:
        column = min(
            int(math.floor((point.position[0] - bounds.min_x) / cell_width)),
            columns - 1,
        )
        row = min(
            int(math.floor((point.position[1] - bounds.min_y) / cell_height)),
            rows - 1,
        )
        buckets[(column, row)].append(point.ordinal)
    return _PointIndex(
        points=points,
        bounds=bounds,
        cell_width=cell_width,
        cell_height=cell_height,
        columns=columns,
        rows=rows,
        buckets={key: tuple(value) for key, value in buckets.items()},
    )


def _region_bounds(regions: tuple[RegionSummary, ...]) -> Bounds3D | None:
    if not regions:
        return None
    return Bounds3D(
        min_x=min(item.min_x for item in regions),
        min_y=min(item.min_y for item in regions),
        min_z=min(item.min_z for item in regions),
        max_x=max(item.max_x for item in regions),
        max_y=max(item.max_y for item in regions),
        max_z=max(item.max_z for item in regions),
    )


def _point_bounds(points: tuple[_IndexedPoint, ...]) -> Bounds3D | None:
    if not points:
        return None
    return Bounds3D(
        min_x=min(item.position[0] for item in points),
        min_y=min(item.position[1] for item in points),
        min_z=min(item.position[2] for item in points),
        max_x=max(item.position[0] for item in points),
        max_y=max(item.position[1] for item in points),
        max_z=max(item.position[2] for item in points),
    )


def _point_set_bounds(point_set: PointSetInfo) -> Bounds3D | None:
    values = (
        point_set.min_x,
        point_set.min_y,
        point_set.min_z,
        point_set.max_x,
        point_set.max_y,
        point_set.max_z,
    )
    if all(value is None for value in values):
        return None
    if any(value is None for value in values):
        raise ViewerProjectionError("PointSet bounds are incomplete")
    return Bounds3D(*(float(value) for value in values))


def _bounds_from_region(region: RegionSummary) -> Bounds3D:
    return Bounds3D(
        region.min_x,
        region.min_y,
        region.min_z,
        region.max_x,
        region.max_y,
        region.max_z,
    )


def _validate_assignment_lineage(
    point_set: PointSetInfo,
    assignment: AssetAssignmentSetInfo,
) -> None:
    if assignment.point_set_id not in {
        point_set.point_set_id,
        point_set.parent_point_set_id,
    }:
        raise ValueError("assignment_set_id does not belong to point_set_id")
    if assignment.point_count != point_set.point_count:
        raise ViewerProjectionError("AssignmentSet point count differs from PointSet")


def _validate_viewport(
    min_x: float,
    min_y: float,
    max_x: float,
    max_y: float,
) -> None:
    if not all(math.isfinite(value) for value in (min_x, min_y, max_x, max_y)):
        raise ValueError("viewport coordinates must be finite")
    if min_x > max_x or min_y > max_y:
        raise ValueError("viewport minimum cannot exceed maximum")


def _validate_page(after_ordinal: int, limit: int) -> None:
    if after_ordinal < -1:
        raise ValueError("after_ordinal must be at least -1")
    if limit <= 0:
        raise ValueError("limit must be positive")


def _validate_simplification_tolerance(value: float) -> None:
    if not math.isfinite(value) or value < 0.0:
        raise ValueError(
            "simplification_tolerance must be finite and non-negative"
        )


def _intersects(
    bounds: Bounds3D,
    *,
    min_x: float,
    min_y: float,
    max_x: float,
    max_y: float,
) -> bool:
    return not (
        bounds.max_x < min_x
        or bounds.min_x > max_x
        or bounds.max_y < min_y
        or bounds.min_y > max_y
    )


def _required_text(value: dict[str, object], field: str, label: str) -> str:
    result = value.get(field)
    if not isinstance(result, str) or not result:
        raise ViewerProjectionError(f"{label} {field} is required")
    return result


def _vector3(value: object, label: str) -> tuple[float, float, float]:
    if (
        not isinstance(value, list)
        or len(value) != 3
        or any(
            not isinstance(item, (int, float)) or isinstance(item, bool)
            for item in value
        )
    ):
        raise ViewerProjectionError(f"{label} must contain three numbers")
    result = tuple(float(item) for item in value)
    if not all(math.isfinite(item) for item in result):
        raise ViewerProjectionError(f"{label} must contain finite numbers")
    return result
