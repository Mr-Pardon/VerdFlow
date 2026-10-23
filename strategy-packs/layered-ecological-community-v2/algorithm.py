"""Deterministic two-role ecological community algorithm."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
import hashlib
import math
from typing import Callable, Iterator


Point = tuple[float, float]
Edge = tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class RegionShape:
    region_id: str
    outer: tuple[Point, ...]
    holes: tuple[tuple[Point, ...], ...] = ()


@dataclass(frozen=True, slots=True)
class PreparedRing:
    min_x: float
    min_y: float
    max_x: float
    max_y: float
    bucket_height: float
    buckets: tuple[tuple[Edge, ...], ...]


@dataclass(frozen=True, slots=True)
class PreparedRegion:
    outer: PreparedRing
    holes: tuple[PreparedRing, ...]


@dataclass(frozen=True, slots=True)
class CommunityParameters:
    groundcover_coverage_percent: float = 85.0
    focal_core_ratio_percent: float = 55.0
    groundcover_spacing_m: float = 3.4
    focal_spacing_m: float = 11.0
    patch_scale_m: float = 48.0


@dataclass(frozen=True, slots=True)
class CommunityPoint:
    x: float
    y: float
    role_key: str


@dataclass(frozen=True, slots=True)
class CommunityResult:
    region_id: str
    seed: int
    points: tuple[CommunityPoint, ...]
    counts: tuple[tuple[str, int], ...]


ROLE_ORDER = ("groundcover_matrix", "focal_structure")
EDGE_SOFTNESS = 0.075
FOCAL_EDGE_SOFTNESS = EDGE_SOFTNESS * 0.72
MACRO_TEXTURE_TEXELS_PER_PATCH = 16.0
FIELD_SAMPLE_BUDGET = 2048
NOISE_CELL_CACHE_SIZE = 65_536


@dataclass(slots=True)
class DensityTexture:
    """Lazily rasterize a continuous field on a world-aligned grid."""

    evaluator: Callable[[float, float], float]
    texel_size: float
    _values: dict[tuple[int, int], float] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        if not math.isfinite(self.texel_size) or self.texel_size <= 0.0:
            raise ValueError("Density texture texel_size must be positive and finite")

    @property
    def evaluated_texel_count(self) -> int:
        return len(self._values)

    def __call__(self, x: float, y: float) -> float:
        grid_x = x / self.texel_size
        grid_y = y / self.texel_size
        x0 = math.floor(grid_x)
        y0 = math.floor(grid_y)
        tx = _smoothstep(grid_x - x0)
        ty = _smoothstep(grid_y - y0)
        a = self._texel(x0, y0)
        b = self._texel(x0 + 1, y0)
        c = self._texel(x0, y0 + 1)
        d = self._texel(x0 + 1, y0 + 1)
        return _lerp(_lerp(a, b, tx), _lerp(c, d, tx), ty)

    def _texel(self, x: int, y: int) -> float:
        key = (x, y)
        value = self._values.get(key)
        if value is None:
            value = self.evaluator(x * self.texel_size, y * self.texel_size)
            self._values[key] = value
        return value


def generate_community(
    region: RegionShape,
    parameters: CommunityParameters,
    *,
    seed: int,
    enabled_role_keys: tuple[str, ...] | None = None,
) -> CommunityResult:
    _validate_region(region)
    _validate_parameters(parameters)
    enabled_roles = _normalize_enabled_roles(enabled_role_keys)
    if not enabled_roles:
        return CommunityResult(
            region_id=region.region_id,
            seed=seed,
            points=(),
            counts=tuple((role, 0) for role in ROLE_ORDER),
        )

    prepared_region = _prepare_region(region)
    shared = DensityTexture(
        _macro_field(seed, parameters.patch_scale_m),
        _macro_texture_texel_size(parameters.patch_scale_m),
    )
    samples = _field_samples(
        region,
        _field_sample_spacing(region, parameters.patch_scale_m),
        prepared_region,
    )
    shared_values = tuple(shared(*point) for point in samples)
    ground_target = (
        parameters.groundcover_coverage_percent / 100.0
        if "groundcover_matrix" in enabled_roles
        else 1.0
    )
    focal_target = ground_target * parameters.focal_core_ratio_percent / 100.0
    ground_threshold = _quantile(shared_values, 1.0 - ground_target)
    focal_threshold = max(
        ground_threshold,
        _quantile(shared_values, 1.0 - focal_target),
    )

    by_role: dict[str, tuple[CommunityPoint, ...]] = {
        role: () for role in ROLE_ORDER
    }
    if "groundcover_matrix" in enabled_roles:
        by_role["groundcover_matrix"] = _generate_role(
            region,
            prepared_region,
            role_key="groundcover_matrix",
            spacing=parameters.groundcover_spacing_m,
            seed=_derived_seed(seed, "groundcover-points"),
            shared=shared,
            detail=_detail_field(seed, "groundcover", parameters.patch_scale_m),
            threshold=ground_threshold,
            edge_softness=EDGE_SOFTNESS,
        )
    if "focal_structure" in enabled_roles:
        by_role["focal_structure"] = _generate_role(
            region,
            prepared_region,
            role_key="focal_structure",
            spacing=parameters.focal_spacing_m,
            seed=_derived_seed(seed, "focal-points"),
            shared=shared,
            detail=_detail_field(seed, "focal", parameters.patch_scale_m),
            threshold=focal_threshold,
            edge_softness=FOCAL_EDGE_SOFTNESS,
        )
    return CommunityResult(
        region_id=region.region_id,
        seed=seed,
        points=tuple(point for role in ROLE_ORDER for point in by_role[role]),
        counts=tuple((role, len(by_role[role])) for role in ROLE_ORDER),
    )


def point_in_region(point: Point, region: RegionShape) -> bool:
    return _point_in_prepared_region(point, _prepare_region(region))


def _generate_role(
    region: RegionShape,
    prepared_region: PreparedRegion,
    *,
    role_key: str,
    spacing: float,
    seed: int,
    shared: Callable[[float, float], float],
    detail: Callable[[float, float], float],
    threshold: float,
    edge_softness: float,
) -> tuple[CommunityPoint, ...]:
    best_supported: tuple[float, float, float, float] | None = None
    accepted: list[CommunityPoint] = []
    for x, y, row, column in _role_candidates(
        region,
        prepared_region,
        spacing,
        seed,
    ):
        field_value = shared(x, y)
        if field_value < threshold:
            continue
        detail_value = detail(x, y)
        score = (field_value, detail_value)
        if best_supported is None or score > best_supported[2:]:
            best_supported = (x, y, field_value, detail_value)
        probability = _acceptance_probability(
            field_value,
            detail_value,
            threshold,
            edge_softness,
        )
        if _unit_value(seed, row, column, 3) <= probability:
            accepted.append(CommunityPoint(x, y, role_key))
    if accepted or best_supported is None:
        return tuple(accepted)

    x, y, _field_value, _detail_value = best_supported
    return (CommunityPoint(x, y, role_key),)


def _role_candidates(
    region: RegionShape,
    prepared_region: PreparedRegion,
    spacing: float,
    seed: int,
) -> Iterator[tuple[float, float, int, int]]:
    min_x, min_y, max_x, max_y = _region_bounds(region)
    row_spacing = spacing * math.sqrt(3.0) / 2.0
    min_row = math.floor(min_y / row_spacing) - 1
    max_row = math.ceil(max_y / row_spacing) + 1
    min_column = math.floor(min_x / spacing) - 1
    max_column = math.ceil(max_x / spacing) + 1
    for row in range(min_row, max_row + 1):
        offset = spacing * 0.5 if row % 2 else 0.0
        for column in range(min_column, max_column + 1):
            jitter_x = (
                _unit_value(seed, row, column, 1) - 0.5
            ) * spacing * 0.46
            jitter_y = (
                _unit_value(seed, row, column, 2) - 0.5
            ) * row_spacing * 0.46
            x = column * spacing + offset + jitter_x
            y = row * row_spacing + jitter_y
            if not (min_x <= x <= max_x and min_y <= y <= max_y):
                continue
            if _point_in_prepared_region((x, y), prepared_region):
                yield x, y, row, column


def _acceptance_probability(
    field_value: float,
    detail_value: float,
    threshold: float,
    softness: float,
) -> float:
    strength = _smoothstep_range(
        threshold,
        min(1.0, threshold + max(softness, 0.001)),
        field_value,
    )
    return min(1.0, 0.34 + strength * 0.52 + detail_value * 0.14)


def _macro_field(seed: int, scale: float) -> Callable[[float, float], float]:
    warp_x_seed = _derived_seed(seed, "macro-warp-x")
    warp_y_seed = _derived_seed(seed, "macro-warp-y")
    macro_seed = _derived_seed(seed, "macro")
    texture_seed = _derived_seed(seed, "macro-texture")

    def field(x: float, y: float) -> float:
        warp_scale = scale * 2.4
        warp_x = (
            _fbm(x / warp_scale, y / warp_scale, warp_x_seed) - 0.5
        ) * scale * 0.52
        warp_y = (
            _fbm(x / warp_scale, y / warp_scale, warp_y_seed) - 0.5
        ) * scale * 0.52
        warped_x = x + warp_x
        warped_y = y + warp_y
        macro = _fbm(warped_x / scale, warped_y / scale, macro_seed)
        texture = _fbm(
            warped_x / (scale * 0.43),
            warped_y / (scale * 0.43),
            texture_seed,
        )
        return macro * 0.78 + texture * 0.22

    return field


def _macro_texture_texel_size(patch_scale: float) -> float:
    return max(0.5, patch_scale / MACRO_TEXTURE_TEXELS_PER_PATCH)


def _detail_field(seed: int, role: str, scale: float) -> Callable[[float, float], float]:
    detail_seed = _derived_seed(seed, role, "detail")

    def field(x: float, y: float) -> float:
        return _fbm(x / (scale * 0.31), y / (scale * 0.31), detail_seed)

    return field


def _field_samples(
    region: RegionShape,
    spacing: float,
    prepared_region: PreparedRegion,
) -> tuple[Point, ...]:
    min_x, min_y, max_x, max_y = _region_bounds(region)
    columns = max(4, math.ceil((max_x - min_x) / spacing))
    rows = max(4, math.ceil((max_y - min_y) / spacing))
    step_x = (max_x - min_x) / columns
    step_y = (max_y - min_y) / rows
    samples = tuple(
        (min_x + (column + 0.5) * step_x, min_y + (row + 0.5) * step_y)
        for row in range(rows)
        for column in range(columns)
        if _point_in_prepared_region(
            (min_x + (column + 0.5) * step_x, min_y + (row + 0.5) * step_y),
            prepared_region,
        )
    )
    if samples:
        return samples
    center = (
        sum(point[0] for point in region.outer) / len(region.outer),
        sum(point[1] for point in region.outer) / len(region.outer),
    )
    return (
        (center,)
        if _point_in_prepared_region(center, prepared_region)
        else (region.outer[0],)
    )


def _field_sample_spacing(region: RegionShape, patch_scale: float) -> float:
    min_x, min_y, max_x, max_y = _region_bounds(region)
    bounding_area = max(0.0, (max_x - min_x) * (max_y - min_y))
    bounded_spacing = math.sqrt(bounding_area / FIELD_SAMPLE_BUDGET)
    return max(min(4.0, patch_scale / 10.0), bounded_spacing)


def _quantile(values: tuple[float, ...], quantile: float) -> float:
    ordered = sorted(values)
    position = min(max(quantile, 0.0), 1.0) * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _region_bounds(region: RegionShape) -> tuple[float, float, float, float]:
    xs = [point[0] for point in region.outer]
    ys = [point[1] for point in region.outer]
    return min(xs), min(ys), max(xs), max(ys)


def _fbm(x: float, y: float, seed: int) -> float:
    total = 0.0
    amplitude = 1.0
    amplitude_sum = 0.0
    frequency = 1.0
    for octave in range(4):
        sample_x = x * frequency
        sample_y = y * frequency
        x0 = math.floor(sample_x)
        y0 = math.floor(sample_y)
        tx = sample_x - x0
        ty = sample_y - y0
        tx = tx * tx * (3.0 - 2.0 * tx)
        ty = ty * ty * (3.0 - 2.0 * ty)
        a, b, c, d = _noise_cell(x0, y0, seed + octave * 7919)
        lower = a + (b - a) * tx
        upper = c + (d - c) * tx
        total += (lower + (upper - lower) * ty) * amplitude
        amplitude_sum += amplitude
        amplitude *= 0.52
        frequency *= 2.03
    return total / amplitude_sum


@lru_cache(maxsize=NOISE_CELL_CACHE_SIZE)
def _noise_cell(x: int, y: int, seed: int) -> tuple[float, float, float, float]:
    """Reuse seeded lattice corners across texels without changing interpolation."""
    return (
        _lattice_value(x, y, seed),
        _lattice_value(x + 1, y, seed),
        _lattice_value(x, y + 1, seed),
        _lattice_value(x + 1, y + 1, seed),
    )


def _lattice_value(x: int, y: int, seed: int) -> float:
    value = (x * 374761393 + y * 668265263 + seed * 2246822519) & 0xFFFFFFFF
    value = (value ^ (value >> 13)) * 1274126177 & 0xFFFFFFFF
    value ^= value >> 16
    return value / 0xFFFFFFFF


def _unit_value(seed: int, *parts: int) -> float:
    payload = "\x1f".join((str(seed), *(str(part) for part in parts))).encode("ascii")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") / (2**64 - 1)


def _derived_seed(seed: int, *parts: str) -> int:
    payload = "\x1f".join((str(seed), *parts)).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")


def _smoothstep(value: float) -> float:
    return value * value * (3.0 - 2.0 * value)


def _smoothstep_range(start: float, end: float, value: float) -> float:
    if end <= start:
        return 1.0 if value >= end else 0.0
    amount = min(max((value - start) / (end - start), 0.0), 1.0)
    return _smoothstep(amount)


def _lerp(start: float, end: float, amount: float) -> float:
    return start + (end - start) * amount


def _signed_area(points: tuple[Point, ...]) -> float:
    origin_x, origin_y = points[0]
    return 0.5 * math.fsum(
        (current[0] - origin_x) * (following[1] - origin_y)
        - (following[0] - origin_x) * (current[1] - origin_y)
        for current, following in zip(points, points[1:] + points[:1])
    )


def _prepare_region(region: RegionShape) -> PreparedRegion:
    return PreparedRegion(
        outer=_prepare_ring(region.outer),
        holes=tuple(_prepare_ring(hole) for hole in region.holes),
    )


def _prepare_ring(ring: tuple[Point, ...]) -> PreparedRing:
    xs = tuple(point[0] for point in ring)
    ys = tuple(point[1] for point in ring)
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    height = max_y - min_y
    bucket_count = min(1024, max(8, math.ceil(math.sqrt(len(ring)) * 2.0)))
    edges: list[tuple[Edge, float, float]] = []
    previous = ring[-1]
    for current in ring:
        if current[1] != previous[1]:
            low_y = min(current[1], previous[1])
            high_y = max(current[1], previous[1])
            edge = (current[0], current[1], previous[0], previous[1])
            edges.append((edge, low_y, high_y))
        previous = current

    while bucket_count > 8:
        bucket_height = height / bucket_count if height > 0.0 else 1.0
        reference_count = sum(
            _bucket_index(high_y, min_y, bucket_height, bucket_count)
            - _bucket_index(low_y, min_y, bucket_height, bucket_count)
            + 1
            for _edge, low_y, high_y in edges
        )
        if reference_count <= max(1, len(edges)) * 8:
            break
        bucket_count = max(8, bucket_count // 2)

    bucket_height = height / bucket_count if height > 0.0 else 1.0
    buckets: list[list[Edge]] = [[] for _ in range(bucket_count)]
    for edge, low_y, high_y in edges:
        first_bucket = _bucket_index(low_y, min_y, bucket_height, bucket_count)
        last_bucket = _bucket_index(high_y, min_y, bucket_height, bucket_count)
        for bucket_index in range(first_bucket, last_bucket + 1):
            buckets[bucket_index].append(edge)

    return PreparedRing(
        min_x=min_x,
        min_y=min_y,
        max_x=max_x,
        max_y=max_y,
        bucket_height=bucket_height,
        buckets=tuple(tuple(bucket) for bucket in buckets),
    )


def _point_in_prepared_region(point: Point, region: PreparedRegion) -> bool:
    return _point_in_prepared_ring(point, region.outer) and not any(
        _point_in_prepared_ring(point, hole) for hole in region.holes
    )


def _point_in_prepared_ring(point: Point, ring: PreparedRing) -> bool:
    x, y = point
    if x < ring.min_x or x > ring.max_x or y < ring.min_y or y > ring.max_y:
        return False

    bucket_index = _bucket_index(
        y,
        ring.min_y,
        ring.bucket_height,
        len(ring.buckets),
    )
    inside = False
    for current_x, current_y, prior_x, prior_y in ring.buckets[bucket_index]:
        if (current_y > y) != (prior_y > y):
            crossing_x = (
                (prior_x - current_x)
                * (y - current_y)
                / (prior_y - current_y)
                + current_x
            )
            if x < crossing_x:
                inside = not inside
    return inside


def _bucket_index(
    y: float,
    min_y: float,
    bucket_height: float,
    bucket_count: int,
) -> int:
    return min(bucket_count - 1, max(0, int((y - min_y) / bucket_height)))


def _validate_region(region: RegionShape) -> None:
    if len(region.outer) < 3 or any(len(hole) < 3 for hole in region.holes):
        raise ValueError("Region rings require at least three points")
    outer_area = abs(_signed_area(region.outer))
    hole_area = math.fsum(abs(_signed_area(hole)) for hole in region.holes)
    if outer_area - hole_area <= 0.0:
        raise ValueError(
            f"Region {region.region_id!r} usable XY area must be positive "
            f"(outer={outer_area:.6g}, holes={hole_area:.6g})"
        )


def _validate_parameters(parameters: CommunityParameters) -> None:
    if not 5.0 <= parameters.groundcover_coverage_percent <= 98.0:
        raise ValueError("groundcover_coverage_percent must be between 5 and 98")
    if not 1.0 <= parameters.focal_core_ratio_percent <= 100.0:
        raise ValueError("focal_core_ratio_percent must be between 1 and 100")
    for name in ("groundcover_spacing_m", "focal_spacing_m", "patch_scale_m"):
        value = getattr(parameters, name)
        if not math.isfinite(value) or value <= 0.0:
            raise ValueError(f"{name} must be positive and finite")


def _normalize_enabled_roles(
    enabled_role_keys: tuple[str, ...] | None,
) -> frozenset[str]:
    if enabled_role_keys is None:
        return frozenset(ROLE_ORDER)
    if not isinstance(enabled_role_keys, tuple):
        raise ValueError("enabled_role_keys must be a tuple")
    if len(set(enabled_role_keys)) != len(enabled_role_keys):
        raise ValueError("enabled_role_keys cannot contain duplicates")
    unknown = sorted(set(enabled_role_keys) - set(ROLE_ORDER))
    if unknown:
        raise ValueError(f"Unknown enabled role: {unknown[0]}")
    return frozenset(enabled_role_keys)
