"""Deterministic lawn and feature-group planting composition."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import random


Point = tuple[float, float]


@dataclass(frozen=True, slots=True)
class RegionShape:
    region_id: str
    outer: tuple[Point, ...]
    holes: tuple[tuple[Point, ...], ...] = ()


@dataclass(frozen=True, slots=True)
class PlantingParameters:
    groundcover_spacing_m: float = 1.2
    feature_group_density: float = 3.0
    feature_anchor_probability: float = 0.75
    feature_group_min_count: float = 0.0
    feature_group_max_count: float = 0.0


@dataclass(frozen=True, slots=True)
class PlantingPoint:
    x: float
    y: float
    role_key: str
    group_id: str


@dataclass(frozen=True, slots=True)
class CompositionResult:
    region_id: str
    seed: int
    points: tuple[PlantingPoint, ...]
    counts: tuple[tuple[str, int], ...]
    feature_group_count: int
    feature_group_mode: str


ROLE_ORDER = (
    "lawn_groundcover",
    "edge_groundcover",
    "feature_anchor",
    "surrounding_shrub",
    "outer_accent",
)
MAIN_GROUNDCOVER_INSET_M = 0.5
EDGE_GROUNDCOVER_SPACING_M = 0.5
MAIN_GROUNDCOVER_JITTER_RATIO = 0.16
EDGE_GROUNDCOVER_JITTER_RATIO = 0.12
FULL_GROUP_CLEARANCE_M = 3.0
GROUP_BOUNDARY_MARGIN_M = 0.35
BASE_GROUP_INTERVAL_M = 38.0
GROUP_DENSITY_CALIBRATION = 0.75
GROUP_CANDIDATE_STEP_M = 2.0
GROUP_CENTER_MIN_DISTANCE_M = 4.2
GROUP_CENTER_RELAXATION_PASSES = 8
GROUP_CENTER_COMFORT_FACTOR = 0.22
FULL_ANCHORED_SHRUB_RADIUS_M = (1.05, 1.45)
FULL_ANCHORLESS_SHRUB_RADIUS_M = (0.55, 1.05)
FULL_ANCHORED_ACCENT_RADIUS_M = (1.55, 2.05)
FULL_ANCHORLESS_ACCENT_RADIUS_M = (1.2, 1.7)


def generate_composition(
    region: RegionShape,
    parameters: PlantingParameters,
    *,
    seed: int,
) -> CompositionResult:
    """Generate a continuous lawn and sparse compound focal groups."""
    _validate_region(region)
    _validate_parameters(parameters)

    lawn = _generate_groundcover(region, parameters, seed=seed)
    centers, mode = _select_group_centers(region, parameters, seed=seed)
    feature_points: list[PlantingPoint] = []
    for index, center in enumerate(centers, start=1):
        group_id = f"feature_{region.region_id}_{index:04d}"
        feature_points.extend(
            _build_feature_group(
                center,
                group_id=group_id,
                anchor_probability=parameters.feature_anchor_probability,
                seed=_derived_seed(seed, region.region_id, group_id),
            )
        )

    points = tuple((*lawn, *feature_points))
    counts = tuple(
        (role_key, sum(point.role_key == role_key for point in points))
        for role_key in ROLE_ORDER
    )
    return CompositionResult(
        region_id=region.region_id,
        seed=seed,
        points=points,
        counts=counts,
        feature_group_count=len(centers),
        feature_group_mode=mode,
    )


def region_area(region: RegionShape) -> float:
    return max(
        0.0,
        abs(_signed_area(region.outer))
        - sum(abs(_signed_area(hole)) for hole in region.holes),
    )


def region_bounds(region: RegionShape) -> tuple[float, float, float, float]:
    xs = [point[0] for point in region.outer]
    ys = [point[1] for point in region.outer]
    return min(xs), min(ys), max(xs), max(ys)


def point_in_region(point: Point, region: RegionShape) -> bool:
    return _point_in_ring(point, region.outer) and not any(
        _point_in_ring(point, hole) for hole in region.holes
    )


def distance_to_region_boundary(point: Point, region: RegionShape) -> float:
    rings = (region.outer, *region.holes)
    return min(
        _distance_point_segment(point, start, end)
        for ring in rings
        for start, end in zip(ring, ring[1:] + ring[:1])
    )


def _generate_groundcover(
    region: RegionShape,
    parameters: PlantingParameters,
    *,
    seed: int,
) -> tuple[PlantingPoint, ...]:
    main = _generate_groundcover_layer(
        region,
        spacing=parameters.groundcover_spacing_m,
        minimum_boundary_distance=MAIN_GROUNDCOVER_INSET_M,
        maximum_boundary_distance=None,
        jitter_ratio=MAIN_GROUNDCOVER_JITTER_RATIO,
        role_key="lawn_groundcover",
        seed=_derived_seed(seed, region.region_id, "main-groundcover"),
    )
    edge = _generate_groundcover_layer(
        region,
        spacing=EDGE_GROUNDCOVER_SPACING_M,
        minimum_boundary_distance=0.0,
        maximum_boundary_distance=MAIN_GROUNDCOVER_INSET_M,
        jitter_ratio=EDGE_GROUNDCOVER_JITTER_RATIO,
        role_key="edge_groundcover",
        seed=_derived_seed(seed, region.region_id, "edge-groundcover"),
    )
    return (*main, *edge)


def _generate_groundcover_layer(
    region: RegionShape,
    *,
    spacing: float,
    minimum_boundary_distance: float,
    maximum_boundary_distance: float | None,
    jitter_ratio: float,
    role_key: str,
    seed: int,
) -> tuple[PlantingPoint, ...]:
    row_spacing = spacing * math.sqrt(3.0) / 2.0
    rng = random.Random(seed)
    angle = rng.uniform(-math.pi / 12.0, math.pi / 12.0)
    phase_x = rng.uniform(0.0, spacing)
    phase_y = rng.uniform(0.0, row_spacing)
    min_x, min_y, max_x, max_y = region_bounds(region)
    origin = ((min_x + max_x) / 2.0, (min_y + max_y) / 2.0)
    local_corners = [
        _rotate((x, y), origin, -angle)
        for x, y in (
            (min_x, min_y),
            (min_x, max_y),
            (max_x, min_y),
            (max_x, max_y),
        )
    ]
    local_min_x = min(point[0] for point in local_corners) - spacing
    local_max_x = max(point[0] for point in local_corners) + spacing
    local_min_y = min(point[1] for point in local_corners) - row_spacing
    local_max_y = max(point[1] for point in local_corners) + row_spacing
    first_row = math.floor((local_min_y - phase_y) / row_spacing)
    last_row = math.ceil((local_max_y - phase_y) / row_spacing)
    group_id = f"{role_key}_{region.region_id}"
    points: list[PlantingPoint] = []

    for row in range(first_row, last_row + 1):
        local_y = phase_y + row * row_spacing
        row_phase = phase_x + (spacing / 2.0 if row % 2 else 0.0)
        first_column = math.floor((local_min_x - row_phase) / spacing)
        last_column = math.ceil((local_max_x - row_phase) / spacing)
        for column in range(first_column, last_column + 1):
            jitter = spacing * jitter_ratio
            local_point = (
                row_phase + column * spacing + rng.uniform(-jitter, jitter),
                local_y + rng.uniform(-jitter, jitter),
            )
            point = _rotate(local_point, origin, angle)
            if not point_in_region(point, region):
                continue
            boundary_distance = distance_to_region_boundary(point, region)
            if boundary_distance + 1e-9 < minimum_boundary_distance:
                continue
            if (
                maximum_boundary_distance is not None
                and boundary_distance >= maximum_boundary_distance - 1e-9
            ):
                continue
            points.append(
                PlantingPoint(
                    x=point[0],
                    y=point[1],
                    role_key=role_key,
                    group_id=group_id,
                )
            )
    points.sort(key=lambda point: (round(point.y, 8), round(point.x, 8)))
    return tuple(points)


def _select_group_centers(
    region: RegionShape,
    parameters: PlantingParameters,
    *,
    seed: int,
) -> tuple[tuple[Point, ...], str]:
    if parameters.feature_group_density <= 0.0:
        return (), "lawn_only"
    step = GROUP_CANDIDATE_STEP_M
    candidates = _interior_candidates(region, step=step, seed=seed)
    if not candidates:
        return (), "lawn_only"
    max_clearance = max(clearance for _point, clearance in candidates)
    required_clearance = FULL_GROUP_CLEARANCE_M + GROUP_BOUNDARY_MARGIN_M
    if max_clearance < required_clearance:
        return (), "lawn_only"
    mode = "full"

    eligible = [
        (point, clearance)
        for point, clearance in candidates
        if clearance >= required_clearance
    ]
    if not eligible:
        return (), "lawn_only"

    approximate_axis_length = region_area(region) / max(2.0 * max_clearance, 1e-9)
    natural_count = max(
        1,
        round(
            approximate_axis_length
            / BASE_GROUP_INTERVAL_M
            * parameters.feature_group_density
            * GROUP_DENSITY_CALIBRATION
        ),
    )
    desired_count = natural_count
    minimum_count = int(parameters.feature_group_min_count)
    maximum_count = int(parameters.feature_group_max_count)
    if minimum_count:
        desired_count = max(desired_count, minimum_count)
    if maximum_count:
        desired_count = min(desired_count, maximum_count)
    accepted = _balanced_group_centers(
        eligible,
        desired_count=desired_count,
        required_clearance=required_clearance,
        candidate_step=step,
        seed=_derived_seed(seed, region.region_id, "feature-center-balance"),
    )
    accepted.sort(key=lambda point: (round(point[1], 8), round(point[0], 8)))
    return tuple(accepted), mode


def _interior_candidates(
    region: RegionShape,
    *,
    step: float,
    seed: int,
) -> list[tuple[Point, float]]:
    min_x, min_y, max_x, max_y = region_bounds(region)
    rng = random.Random(_derived_seed(seed, region.region_id, "interior-grid"))
    phase_x = rng.uniform(0.0, step)
    phase_y = rng.uniform(0.0, step)
    first_x = math.floor((min_x - phase_x) / step)
    last_x = math.ceil((max_x - phase_x) / step)
    first_y = math.floor((min_y - phase_y) / step)
    last_y = math.ceil((max_y - phase_y) / step)
    candidates: list[tuple[Point, float]] = []
    for row in range(first_y, last_y + 1):
        y = phase_y + row * step
        for column in range(first_x, last_x + 1):
            point = (phase_x + column * step, y)
            if point_in_region(point, region):
                candidates.append((point, distance_to_region_boundary(point, region)))
    return candidates


def _build_feature_group(
    center: Point,
    *,
    group_id: str,
    anchor_probability: float,
    seed: int,
) -> tuple[PlantingPoint, ...]:
    rng = random.Random(seed)
    rotation = rng.uniform(0.0, math.tau)
    has_anchor = rng.random() < anchor_probability
    points: list[PlantingPoint] = []
    if has_anchor:
        points.append(
            PlantingPoint(
                x=center[0],
                y=center[1],
                role_key="feature_anchor",
                group_id=group_id,
            )
        )
    shrub_count = rng.choice((2, 3, 3, 3, 3, 3, 4))
    shrub_angles = _shrub_angles(shrub_count, rotation, rng)
    for angle in shrub_angles:
        if has_anchor:
            radius_bounds = FULL_ANCHORED_SHRUB_RADIUS_M
        else:
            radius_bounds = FULL_ANCHORLESS_SHRUB_RADIUS_M
        radius = rng.uniform(*radius_bounds)
        points.append(
            _polar_point(
                center,
                radius,
                angle,
                role_key="surrounding_shrub",
                group_id=group_id,
            )
        )

    gaps = _angular_gaps(shrub_angles)
    accent_count = min(len(gaps), rng.choice((2, 2, 3, 3, 3)))
    for _gap, angle in gaps[:accent_count]:
        accent_radius = (
            rng.uniform(*FULL_ANCHORED_ACCENT_RADIUS_M)
            if has_anchor
            else rng.uniform(*FULL_ANCHORLESS_ACCENT_RADIUS_M)
        )
        points.append(
            _polar_point(
                center,
                accent_radius,
                angle + rng.uniform(-0.13, 0.13),
                role_key="outer_accent",
                group_id=group_id,
            )
        )
    return tuple(points)


def _shrub_angles(
    count: int,
    rotation: float,
    rng: random.Random,
) -> tuple[float, ...]:
    templates = {
        2: (0.08, math.pi + 0.23),
        3: (0.0, 2.02, 4.26),
        4: (0.0, 1.42, 3.11, 4.87),
    }
    return tuple(
        rotation + angle + rng.uniform(-0.14, 0.14)
        for angle in templates[count]
    )


def _angular_gaps(angles: tuple[float, ...]) -> list[tuple[float, float]]:
    ordered = sorted(angle % math.tau for angle in angles)
    gaps: list[tuple[float, float]] = []
    for index, start in enumerate(ordered):
        end = ordered[(index + 1) % len(ordered)]
        if index == len(ordered) - 1:
            end += math.tau
        gap = end - start
        gaps.append((gap, (start + gap / 2.0) % math.tau))
    gaps.sort(key=lambda item: item[0], reverse=True)
    return gaps


def _polar_point(
    center: Point,
    radius: float,
    angle: float,
    *,
    role_key: str,
    group_id: str,
) -> PlantingPoint:
    return PlantingPoint(
        x=center[0] + math.cos(angle) * radius,
        y=center[1] + math.sin(angle) * radius,
        role_key=role_key,
        group_id=group_id,
    )


def _rotate(point: Point, origin: Point, angle: float) -> Point:
    dx = point[0] - origin[0]
    dy = point[1] - origin[1]
    cosine = math.cos(angle)
    sine = math.sin(angle)
    return (
        origin[0] + dx * cosine - dy * sine,
        origin[1] + dx * sine + dy * cosine,
    )


def _distance_point_segment(point: Point, start: Point, end: Point) -> float:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length_squared = dx * dx + dy * dy
    if length_squared <= 1e-18:
        return math.dist(point, start)
    amount = max(
        0.0,
        min(
            1.0,
            ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy)
            / length_squared,
        ),
    )
    projection = (start[0] + amount * dx, start[1] + amount * dy)
    return math.dist(point, projection)


def _balanced_group_centers(
    candidates: list[tuple[Point, float]],
    *,
    desired_count: int,
    required_clearance: float,
    candidate_step: float,
    seed: int,
) -> list[Point]:
    if not candidates or desired_count <= 0:
        return []
    max_clearance = max(clearance for _point, clearance in candidates)
    safe_area = len(candidates) * candidate_step**2
    territory_radius = math.sqrt(
        safe_area / (math.pi * max(desired_count, 1))
    )
    comfort_inset = min(
        territory_radius * GROUP_CENTER_COMFORT_FACTOR,
        max(0.0, max_clearance - required_clearance) * 0.8,
    )
    center_candidates = [
        item
        for item in candidates
        if item[1] >= required_clearance + comfort_inset
    ] or candidates
    rng = random.Random(seed)
    tie_breaks = {point: rng.random() for point, _clearance in candidates}

    first, _clearance = max(
        center_candidates,
        key=lambda item: (
            item[1]
            + _center_field(item[0], seed) * territory_radius * 0.08,
            tie_breaks[item[0]],
        ),
    )
    centers = [first]
    while len(centers) < desired_count:
        available = [
            item
            for item in center_candidates
            if all(
                math.dist(item[0], center) >= GROUP_CENTER_MIN_DISTANCE_M
                for center in centers
            )
        ]
        if not available:
            available = [
                item
                for item in candidates
                if all(
                    math.dist(item[0], center) >= GROUP_CENTER_MIN_DISTANCE_M
                    for center in centers
                )
            ]
        if not available:
            break
        point, _clearance = max(
            available,
            key=lambda item: (
                min(math.dist(item[0], center) for center in centers)
                * (0.74 + 0.22 * item[1] / max_clearance)
                * (0.98 + 0.04 * _center_field(item[0], seed)),
                tie_breaks[item[0]],
            ),
        )
        centers.append(point)

    for _pass in range(GROUP_CENTER_RELAXATION_PASSES):
        clusters = _assign_center_candidates(candidates, centers)
        next_centers: list[Point] = []
        for index, cluster in enumerate(clusters):
            if not cluster:
                next_centers.append(centers[index])
                continue
            weighted = [
                (point, 0.84 + 0.32 * _center_field(point, seed))
                for point in cluster
            ]
            total_weight = sum(weight for _point, weight in weighted)
            centroid = (
                sum(point[0] * weight for point, weight in weighted) / total_weight,
                sum(point[1] * weight for point, weight in weighted) / total_weight,
            )
            available = [
                item
                for item in candidates
                if all(
                    math.dist(item[0], accepted) >= GROUP_CENTER_MIN_DISTANCE_M
                    for accepted in next_centers
                )
            ] or [
                item for item in candidates if item[0] not in next_centers
            ] or candidates
            snapped, _clearance = min(
                available,
                key=lambda item: (
                    math.dist(item[0], centroid),
                    tie_breaks[item[0]],
                ),
            )
            next_centers.append(snapped)
        if next_centers == centers:
            break
        centers = next_centers
    return centers


def _assign_center_candidates(
    candidates: list[tuple[Point, float]],
    centers: list[Point],
) -> list[list[Point]]:
    clusters: list[list[Point]] = [[] for _center in centers]
    for point, _clearance in candidates:
        index = min(
            range(len(centers)),
            key=lambda candidate_index: math.dist(point, centers[candidate_index]),
        )
        clusters[index].append(point)
    return clusters


def _center_field(point: Point, seed: int) -> float:
    return _smooth_field(
        point[0] / max(BASE_GROUP_INTERVAL_M * 1.6, 1.0),
        point[1] / max(BASE_GROUP_INTERVAL_M * 1.6, 1.0),
        seed,
    )


def _smooth_field(x: float, y: float, seed: int) -> float:
    total = 0.0
    amplitude = 1.0
    amplitude_sum = 0.0
    frequency = 1.0
    for octave in range(3):
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


def _validate_parameters(parameters: PlantingParameters) -> None:
    for name in (
        "groundcover_spacing_m",
    ):
        value = getattr(parameters, name)
        if not math.isfinite(value) or value <= 0.0:
            raise ValueError(f"{name} must be positive and finite")
    density = parameters.feature_group_density
    if not math.isfinite(density) or density < 0.0:
        raise ValueError("feature_group_density must be non-negative and finite")
    probability = parameters.feature_anchor_probability
    if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
        raise ValueError("feature_anchor_probability must be between 0 and 1")
    minimum_count = parameters.feature_group_min_count
    maximum_count = parameters.feature_group_max_count
    for name, value in (
        ("feature_group_min_count", minimum_count),
        ("feature_group_max_count", maximum_count),
    ):
        if not math.isfinite(value) or value < 0.0 or not value.is_integer():
            raise ValueError(f"{name} must be a non-negative integer")
    if maximum_count and minimum_count > maximum_count:
        raise ValueError(
            "feature_group_min_count cannot exceed feature_group_max_count"
        )
