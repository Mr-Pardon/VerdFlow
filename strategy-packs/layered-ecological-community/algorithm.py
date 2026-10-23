"""Deterministic overlapping vegetation-layer algorithm."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import random
from typing import Callable


Point = tuple[float, float]


@dataclass(frozen=True, slots=True)
class RegionShape:
    region_id: str
    outer: tuple[Point, ...]
    holes: tuple[tuple[Point, ...], ...] = ()


@dataclass(frozen=True, slots=True)
class LayerParameters:
    overall_density: float = 1.0
    canopy_density: float = 1.0
    canopy_spacing_scale: float = 1.0
    canopy_field_contrast: float = 1.75
    shrub_density: float = 1.0
    cluster_scale: float = 1.0
    layer_blend: float = 0.48


@dataclass(frozen=True, slots=True)
class LayerPoint:
    x: float
    y: float
    role_key: str


@dataclass(frozen=True, slots=True)
class LayerResult:
    region_id: str
    seed: int
    points: tuple[LayerPoint, ...]
    counts: tuple[tuple[str, int], ...]


ROLE_ORDER = (
    "low_shrub_matrix",
    "herbaceous_companion",
    "medium_shrub_structure",
    "canopy_anchor",
)


def generate_layers(
    region: RegionShape,
    parameters: LayerParameters,
    *,
    seed: int,
) -> LayerResult:
    _validate_region(region)
    _validate_parameters(parameters)
    area = region_area(region)
    shared_seed = _derived_seed(seed, region.region_id, "shared-field")

    canopy = _generate_role(
        region,
        role_key="canopy_anchor",
        desired_count=_desired_count(
            area,
            spacing=17.0 * parameters.canopy_spacing_scale,
            density=parameters.overall_density * parameters.canopy_density,
        ),
        min_distance=13.5 * parameters.canopy_spacing_scale,
        candidate_multiplier=8.0,
        selection_noise=0.08,
        seed=_derived_seed(seed, region.region_id, "canopy"),
        field=_blended_field(
            shared_seed,
            _derived_seed(seed, region.region_id, "canopy-field"),
            scale=58.0 * parameters.cluster_scale,
            blend=parameters.layer_blend,
            floor=0.08,
            contrast=parameters.canopy_field_contrast,
        ),
    )
    medium_shrub = _generate_role(
        region,
        role_key="medium_shrub_structure",
        desired_count=_desired_count(
            area,
            spacing=7.6,
            density=parameters.overall_density * parameters.shrub_density,
        ),
        min_distance=5.8,
        candidate_multiplier=7.0,
        selection_noise=0.13,
        seed=_derived_seed(seed, region.region_id, "medium-shrub"),
        field=_blended_field(
            shared_seed,
            _derived_seed(seed, region.region_id, "medium-shrub-field"),
            scale=38.0 * parameters.cluster_scale,
            blend=parameters.layer_blend * 0.72,
            floor=0.07,
            contrast=1.85,
        ),
    )
    herbaceous = _generate_role(
        region,
        role_key="herbaceous_companion",
        desired_count=_desired_count(
            area,
            spacing=4.7,
            density=parameters.overall_density,
        ),
        min_distance=3.25,
        candidate_multiplier=5.0,
        selection_noise=0.22,
        seed=_derived_seed(seed, region.region_id, "herbaceous"),
        field=_companion_field(
            shared_seed=shared_seed,
            own_seed=_derived_seed(seed, region.region_id, "herbaceous-field"),
            scale=27.0 * parameters.cluster_scale,
            blend=parameters.layer_blend,
            canopy=canopy,
        ),
    )
    low_matrix = _generate_role(
        region,
        role_key="low_shrub_matrix",
        desired_count=_desired_count(
            area,
            spacing=3.7,
            density=parameters.overall_density,
        ),
        min_distance=2.55,
        candidate_multiplier=3.5,
        selection_noise=0.38,
        seed=_derived_seed(seed, region.region_id, "low-matrix"),
        field=_blended_field(
            shared_seed,
            _derived_seed(seed, region.region_id, "low-matrix-field"),
            scale=21.0 * parameters.cluster_scale,
            blend=parameters.layer_blend * 0.38,
            floor=0.42,
            contrast=1.15,
        ),
    )

    by_role = {
        "low_shrub_matrix": low_matrix,
        "herbaceous_companion": herbaceous,
        "medium_shrub_structure": medium_shrub,
        "canopy_anchor": canopy,
    }
    points = tuple(point for role in ROLE_ORDER for point in by_role[role])
    counts = tuple((role, len(by_role[role])) for role in ROLE_ORDER)
    return LayerResult(
        region_id=region.region_id,
        seed=seed,
        points=points,
        counts=counts,
    )


def region_area(region: RegionShape) -> float:
    return max(
        0.0,
        abs(_signed_area(region.outer))
        - sum(abs(_signed_area(hole)) for hole in region.holes),
    )


def point_in_region(point: Point, region: RegionShape) -> bool:
    return _point_in_ring(point, region.outer) and not any(
        _point_in_ring(point, hole) for hole in region.holes
    )


def region_bounds(region: RegionShape) -> tuple[float, float, float, float]:
    xs = [point[0] for point in region.outer]
    ys = [point[1] for point in region.outer]
    return min(xs), min(ys), max(xs), max(ys)


def _generate_role(
    region: RegionShape,
    *,
    role_key: str,
    desired_count: int,
    min_distance: float,
    candidate_multiplier: float,
    selection_noise: float,
    seed: int,
    field: Callable[[float, float], float],
) -> tuple[LayerPoint, ...]:
    if desired_count <= 0:
        return ()
    rng = random.Random(seed)
    candidates = _ranked_candidates(
        region,
        candidate_count=max(desired_count, round(desired_count * candidate_multiplier)),
        rng=rng,
        field=field,
        selection_noise=selection_noise,
    )
    cell_size = max(min_distance, 0.01)
    grid: dict[tuple[int, int], list[Point]] = {}
    accepted: list[LayerPoint] = []
    for _score, point in candidates:
        if not _far_enough(point, grid, cell_size, min_distance):
            continue
        accepted.append(LayerPoint(point[0], point[1], role_key))
        grid.setdefault(_grid_key(point, cell_size), []).append(point)
        if len(accepted) >= desired_count:
            break
    return tuple(accepted)


def _ranked_candidates(
    region: RegionShape,
    *,
    candidate_count: int,
    rng: random.Random,
    field: Callable[[float, float], float],
    selection_noise: float,
) -> tuple[tuple[float, Point], ...]:
    min_x, min_y, max_x, max_y = region_bounds(region)
    candidates: list[tuple[float, Point]] = []
    max_attempts = max(2400, candidate_count * 4)

    for _ in range(max_attempts):
        if len(candidates) >= candidate_count:
            break
        point = (rng.uniform(min_x, max_x), rng.uniform(min_y, max_y))
        if not point_in_region(point, region):
            continue
        score = field(*point) + rng.uniform(-selection_noise, selection_noise)
        candidates.append((score, point))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return tuple(candidates)


def _blended_field(
    shared_seed: int,
    own_seed: int,
    *,
    scale: float,
    blend: float,
    floor: float,
    contrast: float,
) -> Callable[[float, float], float]:
    def field(x: float, y: float) -> float:
        shared = _fbm(x / scale, y / scale, shared_seed)
        own = _fbm(x / scale, y / scale, own_seed)
        value = shared * blend + own * (1.0 - blend)
        return floor + (1.0 - floor) * value**contrast

    return field


def _companion_field(
    *,
    shared_seed: int,
    own_seed: int,
    scale: float,
    blend: float,
    canopy: tuple[LayerPoint, ...],
) -> Callable[[float, float], float]:
    base = _blended_field(
        shared_seed,
        own_seed,
        scale=scale,
        blend=blend * 0.62,
        floor=0.18,
        contrast=1.55,
    )

    def field(x: float, y: float) -> float:
        if not canopy:
            return base(x, y)
        nearest = min(math.hypot(x - point.x, y - point.y) for point in canopy)
        affinity = math.exp(-((nearest / 20.0) ** 2))
        return min(1.0, base(x, y) * 0.76 + affinity * 0.34)

    return field


def _desired_count(area: float, *, spacing: float, density: float) -> int:
    return max(1, round(area / (spacing * spacing) * 0.68 * density))


def _far_enough(
    point: Point,
    grid: dict[tuple[int, int], list[Point]],
    cell_size: float,
    min_distance: float,
) -> bool:
    key_x, key_y = _grid_key(point, cell_size)
    for x in range(key_x - 1, key_x + 2):
        for y in range(key_y - 1, key_y + 2):
            for existing in grid.get((x, y), ()):
                if math.hypot(point[0] - existing[0], point[1] - existing[1]) < min_distance:
                    return False
    return True


def _grid_key(point: Point, cell_size: float) -> tuple[int, int]:
    return math.floor(point[0] / cell_size), math.floor(point[1] / cell_size)


def _fbm(x: float, y: float, seed: int) -> float:
    total = 0.0
    amplitude = 1.0
    amplitude_sum = 0.0
    frequency = 1.0
    for octave in range(4):
        total += _value_noise(x * frequency, y * frequency, seed + octave * 7919) * amplitude
        amplitude_sum += amplitude
        amplitude *= 0.52
        frequency *= 2.03
    return total / amplitude_sum


def _value_noise(x: float, y: float, seed: int) -> float:
    x0 = math.floor(x)
    y0 = math.floor(y)
    tx = _smoothstep(x - x0)
    ty = _smoothstep(y - y0)
    a = _lattice_value(x0, y0, seed)
    b = _lattice_value(x0 + 1, y0, seed)
    c = _lattice_value(x0, y0 + 1, seed)
    d = _lattice_value(x0 + 1, y0 + 1, seed)
    return _lerp(_lerp(a, b, tx), _lerp(c, d, tx), ty)


def _lattice_value(x: int, y: int, seed: int) -> float:
    value = (x * 374761393 + y * 668265263 + seed * 2246822519) & 0xFFFFFFFF
    value = (value ^ (value >> 13)) * 1274126177 & 0xFFFFFFFF
    value ^= value >> 16
    return value / 0xFFFFFFFF


def _smoothstep(value: float) -> float:
    return value * value * (3.0 - 2.0 * value)


def _lerp(start: float, end: float, amount: float) -> float:
    return start + (end - start) * amount


def _derived_seed(seed: int, *parts: str) -> int:
    payload = "\x1f".join((str(seed), *parts)).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")


def _signed_area(points: tuple[Point, ...]) -> float:
    return 0.5 * sum(
        current[0] * following[1] - following[0] * current[1]
        for current, following in zip(points, points[1:] + points[:1])
    )


def _point_in_ring(point: Point, ring: tuple[Point, ...]) -> bool:
    x, y = point
    inside = False
    previous = len(ring) - 1
    for index, current in enumerate(ring):
        prior = ring[previous]
        if (current[1] > y) != (prior[1] > y):
            crossing_x = (
                (prior[0] - current[0])
                * (y - current[1])
                / ((prior[1] - current[1]) or 1e-12)
                + current[0]
            )
            if x < crossing_x:
                inside = not inside
        previous = index
    return inside


def _validate_region(region: RegionShape) -> None:
    if len(region.outer) < 3:
        raise ValueError("Region outer ring requires at least three points")
    if any(len(hole) < 3 for hole in region.holes):
        raise ValueError("Region holes require at least three points")
    if region_area(region) <= 0.0:
        raise ValueError("Region area must be positive")


def _validate_parameters(parameters: LayerParameters) -> None:
    for name in (
        "overall_density",
        "canopy_density",
        "canopy_spacing_scale",
        "canopy_field_contrast",
        "shrub_density",
        "cluster_scale",
    ):
        value = getattr(parameters, name)
        if not math.isfinite(value) or value <= 0.0:
            raise ValueError(f"{name} must be positive and finite")
    if not 0.0 <= parameters.layer_blend <= 1.0:
        raise ValueError("layer_blend must be between 0 and 1")
