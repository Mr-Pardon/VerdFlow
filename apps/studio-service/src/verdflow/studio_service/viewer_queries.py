"""Bounded product projections for Region and Point viewers."""

from __future__ import annotations

from collections import Counter, OrderedDict, defaultdict
from dataclasses import dataclass
import json
import math
from typing import Callable, Generic, TypeVar

from verdflow.contracts import (
    AssetAssignmentSetInfo,
    PointSetInfo,
    RegionSummary,
)
from verdflow.project_store import ProjectQueryStore

from .models import (
    Bounds3D,
    PointLegendItem,
    PointReviewItem,
    PointReviewManifest,
    PointViewportChunk,
    RegionClassificationSummary,
    RegionViewerItem,
    RegionViewerManifest,
    RegionViewportChunk,
)


CacheValue = TypeVar("CacheValue")


class ViewerProjectionError(RuntimeError):
    """A committed viewer Artifact does not satisfy its stored contract."""


class _LruCache(Generic[CacheValue]):
    def __init__(
        self,
        capacity: int,
        *,
        max_weight: int | None = None,
        weight: Callable[[CacheValue], int] | None = None,
    ) -> None:
        self._capacity = capacity
        self._max_weight = max_weight
        self._weight = weight or (lambda _value: 1)
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
        return value


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

    def __init__(self, store: ProjectQueryStore) -> None:
        self._store = store
        self._region_summaries = _LruCache[tuple[RegionSummary, ...]](4)
        self._region_geometry = _LruCache[dict[str, object]](2)
        self._region_items = _LruCache[dict[str, RegionViewerItem]](8)
        self._point_indexes = _LruCache[_PointIndex](
            16, max_weight=500_000, weight=lambda index: len(index.points),
        )
        self._assignment_maps = _LruCache[dict[str, _Assignment]](
            16, max_weight=500_000, weight=len,
        )
        self._asset_presentations = _LruCache[dict[str, _AssetPresentation]](4)

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
        for region in matches:
            page.append(region)
            if len(page) > limit:
                break
        truncated = len(page) > limit
        if truncated:
            page.pop()
        geometry = self._geometry_by_id(region_set.geometry_artifact_id)
        tolerance = _lod_tolerance(simplification_tolerance)
        item_cache = self._region_item_cache(
            region_set.geometry_artifact_id,
            tolerance,
        )
        items_list: list[RegionViewerItem] = []
        for region in page:
            item = item_cache.get(region.region_id)
            if item is None:
                item = _region_item(
                    region,
                    geometry.get(region.region_id),
                    simplification_tolerance=tolerance,
                )
                item_cache[region.region_id] = item
            items_list.append(item)
        items = tuple(items_list)
        return RegionViewportChunk(
            items=items,
            next_after_ordinal=(items[-1].ordinal if truncated and items else None),
            truncated=truncated,
        )

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
        max_scale_by_asset: defaultdict[str, float] = defaultdict(float)
        for row in self._assignments(assignment.assignment_artifact_id).values():
            max_scale_by_asset[row.asset_id] = max(
                max_scale_by_asset[row.asset_id],
                abs(row.target_scale_xyz[0]),
                abs(row.target_scale_xyz[1]),
            )
        asset_legends = tuple(
            _asset_legend(
                item.asset_id,
                item.point_count,
                asset_presentations.get(item.asset_id),
                max_scale_by_asset.get(item.asset_id, 0.0),
            )
            for item in self._store.list_assignment_assets(
                assignment_set_id
            )
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
        items: list[PointReviewItem] = []
        truncated = False
        for ordinal in index.ordinals_in_viewport(
            min_x=min_x,
            min_y=min_y,
            max_x=max_x,
            max_y=max_y,
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
                raise ViewerProjectionError(
                    f"Assignment is missing for point: {point.point_id}"
                )
            if allowed_assets and assignment.asset_id not in allowed_assets:
                continue
            if len(items) == limit:
                truncated = True
                break
            items.append(
                PointReviewItem(
                    point_id=point.point_id,
                    ordinal=point.ordinal,
                    region_id=point.region_id,
                    role_key=point.role_key,
                    position=point.position,
                    asset_id=assignment.asset_id,
                    target_scale_xyz=assignment.target_scale_xyz,
                    group_id=point.group_id,
                )
            )
        return PointViewportChunk(
            items=tuple(items),
            next_after_ordinal=(
                items[-1].ordinal if truncated and items else None
            ),
            truncated=truncated,
        )

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

    def _region_item_cache(
        self,
        artifact_id: str,
        simplification_tolerance: float,
    ) -> dict[str, RegionViewerItem]:
        key = f"{artifact_id}:{simplification_tolerance:.12g}"
        cached = self._region_items.get(key)
        if cached is not None:
            return cached
        return self._region_items.put(key, {})

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
            result[point_id] = _Assignment(
                asset_id=_required_text(row, "asset_id", "Assignment"),
                target_scale_xyz=_vector3(
                    row.get("target_scale_xyz"),
                    "Assignment target_scale_xyz",
                ),
            )
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
    if not isinstance(value, dict):
        raise ViewerProjectionError(
            f"Region geometry is missing: {region.region_id}"
        )
    loops = value.get("footprint_boundary_xyz", [])
    if not isinstance(loops, list):
        raise ViewerProjectionError(
            f"Region boundary is invalid: {region.region_id}"
        )
    boundaries: list[tuple[tuple[float, float, float], ...]] = []
    for loop in loops:
        if not isinstance(loop, list):
            raise ViewerProjectionError(
                f"Region boundary loop is invalid: {region.region_id}"
            )
        boundary = tuple(
            _vector3(vertex, "Region boundary vertex") for vertex in loop
        )
        boundaries.append(
            _simplify_closed_ring(boundary, simplification_tolerance)
        )
    return RegionViewerItem(
        region_id=region.region_id,
        ordinal=region.ordinal,
        label=region.label,
        classification=region.classification,
        strategy_hint=region.strategy_hint,
        footprint_area_xy=region.footprint_area_xy,
        bounds=_bounds_from_region(region),
        footprint_boundary_xyz=tuple(boundaries),
    )


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
