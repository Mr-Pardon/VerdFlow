from __future__ import annotations

import math
import random
from collections import Counter
from typing import Any


DEFAULT_STRATEGY: dict[str, Any] = {
    "strategy_id": "universal_street_tree",
    "strategy_version": "2.0.0",
    "parameters": {
        "default_seed": 2026071301,
        "shrub_spacing_m": 2.0,
        "tree_spacing_m": 20.0,
        "tree_edge_margin_m": 9.0,
        "tree_clear_radius_m": 0.5,
        "single_row_max_width_m": 1.0,
        "triple_row_min_width_m": 4.0,
        "double_row_offset_m": 0.7,
        "double_edge_clearance_m": 0.18,
        "single_edge_clearance_m": 0.0,
        "width_smoothing_window_m": 8.0,
        "triple_row_offsets_m": [-1.55, 0.0, 1.55],
        "edge_clearance_m": 0.45,
        "centerline_jitter_m": 0.0,
    },
}

ROTATION_SEED_XOR = 0x6A09E667


def deep_merge(base: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    merged: dict[str, Any] = {key: value for key, value in base.items()}
    if not override:
        return merged
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def clean_loop(loop: list[Any]) -> list[list[float]]:
    points = [[float(p[0]), float(p[1]), float(p[2] if len(p) > 2 else 0.0)] for p in loop if isinstance(p, list) and len(p) >= 2]
    if len(points) >= 2 and points[0] == points[-1]:
        points = points[:-1]
    return points


def polygon_area(points: list[list[float]]) -> float:
    if len(points) < 3:
        return 0.0
    total = 0.0
    for idx, point in enumerate(points):
        nxt = points[(idx + 1) % len(points)]
        total += point[0] * nxt[1] - nxt[0] * point[1]
    return abs(total) * 0.5


def point_in_polygon_xy(x: float, y: float, polygon: list[list[float]]) -> bool:
    inside = False
    prev = len(polygon) - 1
    for idx, point in enumerate(polygon):
        a = polygon[prev]
        py = point[1]
        ay = a[1]
        if (py > y) != (ay > y):
            px = point[0]
            ax = a[0]
            hit_x = (ax - px) * (y - py) / ((ay - py) or 1e-12) + px
            if x < hit_x:
                inside = not inside
        prev = idx
    return inside


def distance_to_segment_xy(x: float, y: float, a: list[float], b: list[float]) -> float:
    ax, ay = a[0], a[1]
    bx, by = b[0], b[1]
    dx = bx - ax
    dy = by - ay
    length_sq = dx * dx + dy * dy
    if length_sq <= 1e-12:
        return math.hypot(x - ax, y - ay)
    t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / length_sq))
    return math.hypot(x - (ax + t * dx), y - (ay + t * dy))


def rings_for_region(region: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(region.get("rings"), list) and region["rings"]:
        rings = []
        for idx, ring in enumerate(region["rings"]):
            loop = clean_loop(ring.get("loop", []))
            if len(loop) >= 3:
                rings.append({"index": int(ring.get("index", idx)), "role": str(ring.get("role", "outer")), "loop": loop})
        return rings
    raw_loops = region.get("footprint_boundary_xyz") or region.get("loops") or []
    cleaned_loops = []
    for idx, raw_loop in enumerate(raw_loops):
        loop = clean_loop(raw_loop)
        if len(loop) >= 3:
            cleaned_loops.append({"index": idx, "loop": loop, "area": polygon_area(loop)})
    if not cleaned_loops:
        return []

    # FBX-derived loop order is not reliable: a small degenerate fragment can
    # precede the actual outer boundary. Use the largest valid loop as outer.
    outer_index = max(range(len(cleaned_loops)), key=lambda idx: cleaned_loops[idx]["area"])
    rings = []
    for idx, item in enumerate(cleaned_loops):
        rings.append({
            "index": item["index"],
            "role": "outer" if idx == outer_index else "hole",
            "loop": item["loop"],
        })
    return rings


def point_in_region_xy(x: float, y: float, loops: list[dict[str, Any]]) -> bool:
    outer_hit = False
    hole_hit = False
    for ring in loops:
        hit = point_in_polygon_xy(x, y, ring["loop"])
        if ring["role"] == "hole" and hit:
            hole_hit = True
        elif ring["role"] != "hole" and hit:
            outer_hit = True
    return outer_hit and not hole_hit


def distance_to_region_boundary_xy(x: float, y: float, loops: list[dict[str, Any]]) -> float:
    distances: list[float] = []
    for ring in loops:
        loop = ring["loop"]
        for idx in range(len(loop)):
            distances.append(distance_to_segment_xy(x, y, loop[idx], loop[(idx + 1) % len(loop)]))
    return min(distances) if distances else 0.0


def representative_z(loops: list[dict[str, Any]]) -> float:
    total_area = 0.0
    weighted_z = 0.0
    for ring in loops:
        if ring["role"] == "hole":
            continue
        area = polygon_area(ring["loop"])
        avg_z = sum(point[2] for point in ring["loop"]) / len(ring["loop"])
        total_area += area
        weighted_z += area * avg_z
    if total_area <= 1e-9:
        pts = [point for ring in loops for point in ring["loop"]]
        return sum(point[2] for point in pts) / len(pts) if pts else 0.0
    return weighted_z / total_area


def region_area(loops: list[dict[str, Any]]) -> float:
    total = 0.0
    for ring in loops:
        area = polygon_area(ring["loop"])
        total += -area if ring["role"] == "hole" else area
    return max(0.0, total)


def region_entry(region: dict[str, Any], index: int) -> dict[str, Any] | None:
    loops = rings_for_region(region)
    if not loops:
        return None
    area = region_area(loops)
    if area <= 0.0:
        return None
    points = [point for ring in loops for point in ring["loop"]]
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return {
        "region": region,
        "region_id": str(region.get("id") or region.get("name") or f"region_{index:03d}"),
        "loops": loops,
        "area": area,
        "bbox": [min(xs), min(ys), max(xs), max(ys)],
        "z": representative_z(loops),
        "all_points": points,
    }


def compute_major_axis(points: list[list[float]]) -> dict[str, Any]:
    cx = sum(point[0] for point in points) / len(points)
    cy = sum(point[1] for point in points) / len(points)
    sxx = syy = sxy = 0.0
    for x, y, _ in points:
        dx = x - cx
        dy = y - cy
        sxx += dx * dx
        syy += dy * dy
        sxy += dx * dy
    trace = sxx + syy
    det = math.sqrt(max(0.0, (sxx - syy) * (sxx - syy) + 4.0 * sxy * sxy))
    major_value = 0.5 * (trace + det)
    vx = sxy
    vy = major_value - sxx
    if abs(vx) + abs(vy) < 1e-9:
        vx = 1.0 if sxx >= syy else 0.0
        vy = 0.0 if sxx >= syy else 1.0
    length = math.hypot(vx, vy) or 1.0
    ux = vx / length
    uy = vy / length
    nx = -uy
    ny = ux
    projections = [(point[0] - cx) * ux + (point[1] - cy) * uy for point in points]
    laterals = [(point[0] - cx) * nx + (point[1] - cy) * ny for point in points]
    return {
        "origin": [cx, cy],
        "u": [ux, uy],
        "n": [nx, ny],
        "t_min": min(projections),
        "t_max": max(projections),
        "width_estimate": max(laterals) - min(laterals),
    }


def axis_to_world(axis: dict[str, Any], t: float, lateral: float, z: float) -> list[float]:
    ox, oy = axis["origin"]
    ux, uy = axis["u"]
    nx, ny = axis["n"]
    return [ox + ux * t + nx * lateral, oy + uy * t + ny * lateral, z]


def point_inside_with_clearance(entry: dict[str, Any], x: float, y: float, clearance: float) -> bool:
    return point_in_region_xy(x, y, entry["loops"]) and distance_to_region_boundary_xy(x, y, entry["loops"]) >= clearance


def estimate_local_half_width(entry: dict[str, Any], axis: dict[str, Any], t: float, z: float, max_probe: float = 9.0) -> float:
    half_widths: list[float] = []
    for sign in (-1.0, 1.0):
        last_ok = 0.0
        probe = 0.2
        while probe <= max_probe:
            x, y, _ = axis_to_world(axis, t, sign * probe, z)
            if point_in_region_xy(x, y, entry["loops"]):
                last_ok = probe
                probe += 0.2
                continue
            break
        half_widths.append(last_ok)
    return min(half_widths) if half_widths else 0.0


def axis_coordinates(axis: dict[str, Any], point: list[float]) -> tuple[float, float]:
    dx = point[0] - axis["origin"][0]
    dy = point[1] - axis["origin"][1]
    return (
        dx * axis["u"][0] + dy * axis["u"][1],
        dx * axis["n"][0] + dy * axis["n"][1],
    )


def cross_section_intervals(entry: dict[str, Any], axis: dict[str, Any], t: float, z: float) -> list[tuple[float, float]]:
    """Return interior intervals on the local normal through a longitudinal sample."""
    hits: list[float] = []
    for ring in entry["loops"]:
        loop = ring["loop"]
        for index, point in enumerate(loop):
            other = loop[(index + 1) % len(loop)]
            ta, na = axis_coordinates(axis, point)
            tb, nb = axis_coordinates(axis, other)
            if abs(ta - tb) <= 1e-9:
                continue
            if (ta <= t < tb) or (tb <= t < ta):
                ratio = (t - ta) / (tb - ta)
                hits.append(na + (nb - na) * ratio)
    if len(hits) < 2:
        return []
    hits.sort()
    unique_hits: list[float] = []
    for value in hits:
        if not unique_hits or abs(value - unique_hits[-1]) > 1e-5:
            unique_hits.append(value)
    intervals: list[tuple[float, float]] = []
    for low, high in zip(unique_hits, unique_hits[1:]):
        if high - low <= 1e-6:
            continue
        x, y, _ = axis_to_world(axis, t, (low + high) * 0.5, z)
        if point_in_region_xy(x, y, entry["loops"]):
            intervals.append((low, high))
    return intervals


def build_centerline_paths(entry: dict[str, Any], axis: dict[str, Any], z: float, step_m: float = 0.5) -> list[list[dict[str, float]]]:
    paths: list[list[dict[str, float]]] = []
    current: list[dict[str, float]] = []
    previous_center: float | None = None
    previous_t: float | None = None
    t = float(axis["t_min"])
    t_max = float(axis["t_max"])
    while t <= t_max + 1e-9:
        intervals = cross_section_intervals(entry, axis, t, z)
        if not intervals:
            if current:
                paths.append(current)
                current = []
            previous_center = None
            previous_t = None
            t += step_m
            continue
        if previous_center is None:
            low, high = min(intervals, key=lambda item: (abs((item[0] + item[1]) * 0.5), -(item[1] - item[0])))
        else:
            low, high = min(intervals, key=lambda item: (abs((item[0] + item[1]) * 0.5 - previous_center), -(item[1] - item[0])))
        center = (low + high) * 0.5
        if previous_t is not None and t - previous_t > step_m * 1.5 and current:
            paths.append(current)
            current = []
        x, y, _ = axis_to_world(axis, t, center, z)
        current.append({"x": x, "y": y, "width": high - low, "t": t})
        previous_center = center
        previous_t = t
        t += step_m
    if current:
        paths.append(current)
    return [path for path in paths if len(path) >= 2]


def path_length(path: list[dict[str, float]]) -> float:
    return sum(math.hypot(current["x"] - previous["x"], current["y"] - previous["y"]) for previous, current in zip(path, path[1:]))


def sample_path(path: list[dict[str, float]], distance: float) -> dict[str, float]:
    remaining = max(0.0, distance)
    for previous, current in zip(path, path[1:]):
        segment = math.hypot(current["x"] - previous["x"], current["y"] - previous["y"])
        if segment <= 1e-9:
            continue
        if remaining <= segment:
            ratio = remaining / segment
            tangent_x = (current["x"] - previous["x"]) / segment
            tangent_y = (current["y"] - previous["y"]) / segment
            return {
                "x": previous["x"] + (current["x"] - previous["x"]) * ratio,
                "y": previous["y"] + (current["y"] - previous["y"]) * ratio,
                "width": previous["width"] + (current["width"] - previous["width"]) * ratio,
                "tangent_x": tangent_x,
                "tangent_y": tangent_y,
            }
        remaining -= segment
    previous, current = path[-2], path[-1]
    segment = math.hypot(current["x"] - previous["x"], current["y"] - previous["y"]) or 1.0
    return {"x": current["x"], "y": current["y"], "width": current["width"], "tangent_x": (current["x"] - previous["x"]) / segment, "tangent_y": (current["y"] - previous["y"]) / segment}


def station_distances(length: float, spacing: float, edge_margin: float, *, ensure_one: bool = False) -> list[float]:
    start = edge_margin
    end = length - edge_margin
    if end < start:
        return [length * 0.5] if ensure_one and length > 1e-6 else []
    distances: list[float] = []
    distance = start
    while distance <= end + 1e-9:
        distances.append(distance)
        distance += spacing
    return distances


def median_width(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) * 0.5


def rotation_z_quaternion(rotation_degrees: float) -> list[float]:
    half_radians = math.radians(rotation_degrees) * 0.5
    return [0.0, 0.0, math.sin(half_radians), math.cos(half_radians)]


def make_point(
    point_id: str,
    entry: dict[str, Any],
    x: float,
    y: float,
    z: float,
    rotation: float,
    slot_role: str,
    group_id: str,
) -> dict[str, Any]:
    position = [x, y, z]
    return {
        "id": point_id,
        "region_id": entry["region_id"],
        "role_key": slot_role,
        "generated_position": position,
        "terrain_sample": None,
        "resolved_position": position.copy(),
        "rotation_quaternion_xyzw": rotation_z_quaternion(rotation),
        "group_id": group_id,
    }


def generate_region_points(
    entry: dict[str, Any],
    strategy: dict[str, Any],
    rng: random.Random,
    rotation_rng: random.Random,
    point_index_start: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    params = strategy["parameters"]
    axis = compute_major_axis(entry["all_points"])
    z = float(entry["z"])
    shrub_spacing = float(params.get("shrub_spacing_m", 2.0))
    tree_spacing = float(params.get("tree_spacing_m", 20.0))
    tree_edge_margin = float(params.get("tree_edge_margin_m", 9.0))
    tree_clear_radius = float(params.get("tree_clear_radius_m", 0.5))
    single_row_max_width = float(params.get("single_row_max_width_m", 1.0))
    triple_row_min_width = float(params.get("triple_row_min_width_m", 4.0))
    double_row_offset = float(params.get("double_row_offset_m", 0.7))
    double_edge_clearance = float(params.get("double_edge_clearance_m", 0.18))
    single_edge_clearance = float(params.get("single_edge_clearance_m", 0.0))
    width_smoothing_window = float(params.get("width_smoothing_window_m", 8.0))
    triple_offsets = [float(v) for v in params.get("triple_row_offsets_m", [-1.55, 0.0, 1.55])]
    edge_clearance = float(params.get("edge_clearance_m", 0.45))
    centerline_jitter = float(params.get("centerline_jitter_m", 0.25))

    points: list[dict[str, Any]] = []
    accepted_tree_positions: list[tuple[float, float]] = []
    next_index = point_index_start
    width_samples: list[float] = []
    mode_counts: Counter[str] = Counter()
    centerline_paths = build_centerline_paths(entry, axis, z)
    shrub_samples: list[dict[str, float]] = []
    smoothing_radius = max(0, int(round(width_smoothing_window / max(shrub_spacing * 2.0, 1e-6))))
    for path in centerline_paths:
        length = path_length(path)
        for distance in station_distances(length, tree_spacing, tree_edge_margin, ensure_one=True):
            tree_sample = sample_path(path, min(length, max(0.0, distance + rng.uniform(-centerline_jitter, centerline_jitter))))
            x, y = tree_sample["x"], tree_sample["y"]
            if point_in_region_xy(x, y, entry["loops"]):
                points.append(
                    make_point(
                        f"plant_{next_index:05d}",
                        entry,
                        x,
                        y,
                        z,
                        rotation_rng.uniform(0.0, 360.0),
                        "tree_accent",
                        f"{entry['region_id']}_centerline",
                    )
                )
                accepted_tree_positions.append((x, y))
                next_index += 1
        path_shrub_samples = [sample_path(path, distance) for distance in station_distances(length, shrub_spacing, edge_clearance)]
        for index, station in enumerate(path_shrub_samples):
            nearby = path_shrub_samples[max(0, index - smoothing_radius):index + smoothing_radius + 1]
            station["width"] = median_width([sample["width"] for sample in nearby])
        shrub_samples.extend(path_shrub_samples)

    for station in shrub_samples:
        local_width = station["width"]
        local_half_width = local_width * 0.5
        width_samples.append(local_width)
        near_tree = any(math.hypot(station["x"] - tree_x, station["y"] - tree_y) <= tree_clear_radius for tree_x, tree_y in accepted_tree_positions)
        if local_width >= triple_row_min_width:
            offsets_and_roles = [(triple_offsets[0], "outer_shrub"), (triple_offsets[1], "middle_shrub"), (triple_offsets[2], "outer_shrub")]
            mode = "triple"
        elif local_width <= single_row_max_width:
            offsets_and_roles = [(0.0, "narrow_center_shrub")]
            mode = "single"
        else:
            # Keep two distinct shrub belts inside a 1-4m greenbelt. The lateral
            # offset contracts near a narrow boundary but never crosses it.
            allowed_lateral = max(0.0, local_half_width - double_edge_clearance)
            lateral = min(double_row_offset, allowed_lateral)
            offsets_and_roles = [(-lateral, "double_left_shrub"), (lateral, "double_right_shrub")]
            mode = "double"
        mode_counts[mode] += 1

        for lateral, role in offsets_and_roles:
            if role in {"middle_shrub", "double_left_shrub", "double_right_shrub"} and near_tree:
                continue
            clearance = double_edge_clearance if mode == "double" else single_edge_clearance if mode == "single" else edge_clearance
            if abs(lateral) + clearance > local_half_width:
                continue
            normal_x = -station["tangent_y"]
            normal_y = station["tangent_x"]
            x = station["x"] + normal_x * lateral
            y = station["y"] + normal_y * lateral
            if not point_inside_with_clearance(entry, x, y, clearance):
                continue
            points.append(
                make_point(
                    f"plant_{next_index:05d}",
                    entry,
                    x,
                    y,
                    z,
                    rotation_rng.uniform(0.0, 360.0),
                    role,
                    f"{entry['region_id']}_centerline",
                )
            )
            next_index += 1

    summary = {
        "region_id": entry["region_id"],
        "area": entry["area"],
        "estimated_axis_width_mean": sum(width_samples) / len(width_samples) if width_samples else 0.0,
        "estimated_axis_width_max": max(width_samples) if width_samples else 0.0,
        "tree_count": sum(1 for point in points if point["role_key"] == "tree_accent"),
        "shrub_count": sum(1 for point in points if point["role_key"] != "tree_accent"),
        "mode_counts": dict(mode_counts),
    }
    return points, summary


def validate_points(points: list[dict[str, Any]], entries: list[dict[str, Any]], params: dict[str, Any]) -> dict[str, Any]:
    by_region = {entry["region_id"]: entry for entry in entries}
    edge_clearance = float(params.get("edge_clearance_m", 0.45))
    double_edge_clearance = float(params.get("double_edge_clearance_m", 0.18))
    single_edge_clearance = float(params.get("single_edge_clearance_m", 0.0))
    invalid = []
    min_boundary_distance = None
    for point in points:
        entry = by_region.get(str(point["region_id"]))
        if not entry:
            invalid.append({"id": point["id"], "reason": "missing_region"})
            continue
        x = float(point["generated_position"][0])
        y = float(point["generated_position"][1])
        boundary_distance = distance_to_region_boundary_xy(x, y, entry["loops"])
        role = str(point["role_key"])
        if role != "tree_accent":
            clearance = double_edge_clearance if role.startswith("double_") else single_edge_clearance if role == "narrow_center_shrub" else edge_clearance
            ok = point_inside_with_clearance(entry, x, y, clearance)
        else:
            ok = point_in_region_xy(x, y, entry["loops"])
        if not ok:
            invalid.append({"id": point["id"], "reason": "outside_or_clearance", "distance": boundary_distance})
            continue
        min_boundary_distance = boundary_distance if min_boundary_distance is None else min(min_boundary_distance, boundary_distance)
    return {
        "schema": "vegetation-mcp.planting-validation.v1",
        "checked_point_count": len(points),
        "invalid_count": len(invalid),
        "min_boundary_distance": min_boundary_distance,
        "invalid_points_sample": invalid[:20],
    }


def generate(context: dict[str, Any]) -> dict[str, Any]:
    strategy_override = dict(context.get("strategy") or {})
    args = dict(context.get("args") or {})
    strategy = deep_merge(DEFAULT_STRATEGY, strategy_override)
    if isinstance(args.get("strategy_parameters"), dict):
        strategy["parameters"] = deep_merge(strategy["parameters"], args["strategy_parameters"])

    selected_regions = list(context.get("selected_regions") or [])
    seed = int(context.get("seed", strategy["parameters"]["default_seed"]))
    rng = random.Random(seed)
    rotation_rng = random.Random(seed ^ ROTATION_SEED_XOR)

    entries = []
    for index, region in enumerate(selected_regions, start=1):
        entry = region_entry(region, index)
        if entry:
            entries.append(entry)

    points: list[dict[str, Any]] = []
    next_index = 1
    for entry in entries:
        region_points, _summary = generate_region_points(
            entry, strategy, rng, rotation_rng, next_index
        )
        next_index += len(region_points)
        points.extend(region_points)

    validation = validate_points(points, entries, strategy["parameters"])
    if validation["invalid_count"]:
        raise ValueError(
            f"generated {validation['invalid_count']} points outside their region contract"
        )

    return {
        "schema": "verdflow.points",
        "version": "2",
        "strategy": {
            "strategy_id": strategy.get(
                "strategy_id", DEFAULT_STRATEGY["strategy_id"]
            ),
            "strategy_version": strategy.get(
                "strategy_version", DEFAULT_STRATEGY["strategy_version"]
            ),
        },
        "coordinate_space": "source_scene_world",
        "offset_applied": False,
        "kind": "generated",
        "resolution_policy": "generated_position",
        "point_count": len(points),
        "points": points,
    }
