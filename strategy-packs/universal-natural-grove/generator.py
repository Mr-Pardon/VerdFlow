from __future__ import annotations

import math
import random
from typing import Any


DEFAULT_STRATEGY: dict[str, Any] = {
    "strategy_id": "universal_natural_grove",
    "strategy_version": "2.0.0",
    "role_mix": {
        "distribution_scope": "per_grove_group",
        "roles": [
            {
                "role_key": "low_canopy_filler",
                "ratio": 0.50,
            },
            {
                "role_key": "medium_canopy_accent",
                "ratio": 0.35,
            },
            {
                "role_key": "evergreen_contrast",
                "ratio": 0.15,
            },
        ],
    },
    "distribution_model": {
        "area_classes": {
            "large_patch_area_threshold": 20000.0,
            "small_patch_min_viable_area": 18.0,
        },
        "density_defaults": {
            "target_density_per_area": 0.00735,
            "large_patch_min_point_distance": 1.55,
            "small_patch_min_point_distance": 1.35,
            "edge_buffer": 1.5,
        },
        "large_patch_strategy": {
            "subgrove_area_factor": 24000.0,
            "subgrove_count_min": 3,
            "subgrove_count_max": 10,
            "count_multiple": 20,
            "center_count_per_subgrove": 1,
        },
        "small_patch_strategy": {
            "patch_group_min_size": 4,
            "patch_group_max_size": 10,
            "patch_group_target_area": 10500.0,
            "patch_group_max_radius": 190.0,
            "patch_group_min_viable_area": 3000.0,
            "center_area_factor": 18000.0,
            "center_count_min": 1,
            "center_count_max": 5,
            "count_multiple": 20,
        },
    },
}


def deep_merge(base: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    result: dict[str, Any] = {key: value for key, value in base.items()}
    if not override:
        return result
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def clamp(value: int, min_value: int, max_value: int) -> int:
    return max(min_value, min(max_value, value))


def round_to_multiple(value: float, multiple: int) -> int:
    multiple = max(1, int(multiple))
    return max(multiple, int(round(float(value) / multiple)) * multiple)


def distribute_counts(total: int, parts: int, multiple: int) -> list[int]:
    parts = max(1, parts)
    total = round_to_multiple(total, multiple)
    base = max(multiple, (total // parts // multiple) * multiple)
    counts = [base for _ in range(parts)]
    remaining = total - sum(counts)
    index = 0
    while remaining > 0:
        counts[index % parts] += multiple
        remaining -= multiple
        index += 1
    while remaining < 0 and len(counts) > 1:
        target = max(range(len(counts)), key=lambda item: counts[item])
        if counts[target] <= multiple:
            break
        counts[target] -= multiple
        remaining += multiple
    return counts


def signed_polygon_area(points: list[list[float]]) -> float:
    if len(points) < 3:
        return 0.0
    total = 0.0
    for index, current in enumerate(points):
        nxt = points[(index + 1) % len(points)]
        total += float(current[0]) * float(nxt[1]) - float(nxt[0]) * float(current[1])
    return total * 0.5


def polygon_area(points: list[list[float]]) -> float:
    return abs(signed_polygon_area(points))


def point_in_polygon_xy(x: float, y: float, polygon: list[list[float]]) -> bool:
    inside = False
    previous = len(polygon) - 1
    for index, point in enumerate(polygon):
        prev = polygon[previous]
        py = float(point[1])
        qy = float(prev[1])
        if (py > y) != (qy > y):
            px = float(point[0])
            qx = float(prev[0])
            intersect_x = (qx - px) * (y - py) / ((qy - py) or 1e-12) + px
            if x < intersect_x:
                inside = not inside
        previous = index
    return inside


def distance_to_segment_xy(x: float, y: float, a: list[float], b: list[float]) -> float:
    ax, ay = float(a[0]), float(a[1])
    bx, by = float(b[0]), float(b[1])
    dx = bx - ax
    dy = by - ay
    length_sq = dx * dx + dy * dy
    if length_sq <= 1e-12:
        return math.hypot(x - ax, y - ay)
    t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / length_sq))
    return math.hypot(x - (ax + t * dx), y - (ay + t * dy))


def distance_to_region_boundary_xy(x: float, y: float, loops: list[dict[str, Any]]) -> float:
    distances: list[float] = []
    for ring in loops:
        loop = ring["loop"]
        if len(loop) >= 2:
            distances.extend(distance_to_segment_xy(x, y, loop[i], loop[(i + 1) % len(loop)]) for i in range(len(loop)))
    return min(distances) if distances else 0.0


def point_in_region_xy(x: float, y: float, loops: list[dict[str, Any]]) -> bool:
    outer_hit = False
    hole_hit = False
    for ring in loops:
        role = str(ring.get("role", "outer"))
        hit = point_in_polygon_xy(x, y, ring["loop"])
        if role == "hole" and hit:
            hole_hit = True
        elif role != "hole" and hit:
            outer_hit = True
    return outer_hit and not hole_hit


def point_in_region_with_margin(x: float, y: float, loops: list[dict[str, Any]], margin: float) -> bool:
    return point_in_region_xy(x, y, loops) and distance_to_region_boundary_xy(x, y, loops) >= margin


def clean_loop(loop: list[Any]) -> list[list[float]]:
    points = [[float(p[0]), float(p[1]), float(p[2] if len(p) > 2 else 0.0)] for p in loop if isinstance(p, list) and len(p) >= 2]
    if len(points) >= 2 and points[0] == points[-1]:
        points = points[:-1]
    return points


def rings_for_region(region: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(region.get("rings"), list) and region["rings"]:
        rings = []
        for index, ring in enumerate(region["rings"]):
            loop = clean_loop(ring.get("loop", []))
            if len(loop) >= 3:
                rings.append({"index": int(ring.get("index", index)), "role": str(ring.get("role", "outer")), "loop": loop})
        return rings

    raw_loops = region.get("footprint_boundary_xyz") or region.get("loops") or []
    rings = []
    for index, raw_loop in enumerate(raw_loops):
        loop = clean_loop(raw_loop)
        if len(loop) < 3:
            continue
        role = "outer" if index == 0 else "hole"
        rings.append({"index": index, "role": role, "loop": loop})
    return rings


def representative_z(loops: list[dict[str, Any]]) -> float:
    weighted = 0.0
    area_sum = 0.0
    for ring in loops:
        if ring.get("role") == "hole":
            continue
        loop = ring["loop"]
        area = polygon_area(loop)
        z = sum(float(point[2]) for point in loop) / len(loop)
        weighted += z * area
        area_sum += area
    if area_sum <= 1e-9:
        points = [point for ring in loops for point in ring["loop"]]
        return sum(float(point[2]) for point in points) / len(points) if points else 0.0
    return weighted / area_sum


def region_area(loops: list[dict[str, Any]]) -> float:
    total = 0.0
    for ring in loops:
        area = polygon_area(ring["loop"])
        total += -area if ring.get("role") == "hole" else area
    return max(0.0, total)


def region_sampling_entries(selected_regions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entries = []
    for region in selected_regions:
        loops = rings_for_region(region)
        if not loops:
            continue
        area = region_area(loops)
        if area <= 0:
            continue
        points = [point for ring in loops for point in ring["loop"]]
        xs = [float(point[0]) for point in points]
        ys = [float(point[1]) for point in points]
        region_id = str(region.get("id") or region.get("name") or f"region_{len(entries) + 1:04d}")
        entries.append(
            {
                "region": region,
                "region_id": region_id,
                "loops": loops,
                "area": area,
                "bbox": [min(xs), min(ys), max(xs), max(ys)],
                "z": representative_z(loops),
                "centroid": ((min(xs) + max(xs)) * 0.5, (min(ys) + max(ys)) * 0.5),
            }
        )
    return entries


def sample_valid_point_from_entry(entry: dict[str, Any], rng: random.Random, edge_buffer: float, attempts: int = 80) -> list[float] | None:
    min_x, min_y, max_x, max_y = entry["bbox"]
    for _ in range(attempts):
        x = rng.uniform(min_x, max_x)
        y = rng.uniform(min_y, max_y)
        if point_in_region_with_margin(x, y, entry["loops"], edge_buffer):
            return [x, y, float(entry["z"])]
    return None


def add_point_to_grid(point: dict[str, Any], index: int, grid: dict[tuple[int, int], list[int]], cell_size: float) -> None:
    position = point["generated_position"]
    key = (math.floor(float(position[0]) / cell_size), math.floor(float(position[1]) / cell_size))
    grid.setdefault(key, []).append(index)


def is_far_enough_from_grid(
    x: float,
    y: float,
    accepted: list[dict[str, Any]],
    grid: dict[tuple[int, int], list[int]],
    cell_size: float,
    min_point_distance: float,
) -> bool:
    if min_point_distance <= 0:
        return True
    ix = math.floor(x / cell_size)
    iy = math.floor(y / cell_size)
    span = max(1, int(math.ceil(min_point_distance / cell_size)))
    for gx in range(ix - span, ix + span + 1):
        for gy in range(iy - span, iy + span + 1):
            for index in grid.get((gx, gy), []):
                point = accepted[index]
                position = point["generated_position"]
                if math.hypot(x - float(position[0]), y - float(position[1])) < min_point_distance:
                    return False
    return True


def build_adjacent_patch_groups(entries: list[dict[str, Any]], strategy: dict[str, Any]) -> list[dict[str, Any]]:
    small_cfg = strategy["distribution_model"]["small_patch_strategy"]
    min_size = max(1, int(small_cfg["patch_group_min_size"]))
    max_size = max(min_size, int(small_cfg["patch_group_max_size"]))
    target_area = float(small_cfg["patch_group_target_area"])
    max_radius = float(small_cfg["patch_group_max_radius"])
    min_viable_area = float(small_cfg["patch_group_min_viable_area"])

    unassigned = set(range(len(entries)))
    groups: list[list[int]] = []
    for seed in sorted(unassigned, key=lambda index: float(entries[index]["area"]), reverse=True):
        if seed not in unassigned:
            continue
        sx, sy = entries[seed]["centroid"]
        candidates = []
        for index in list(unassigned):
            if index == seed:
                continue
            x, y = entries[index]["centroid"]
            candidates.append((math.hypot(x - sx, y - sy), index))
        candidates.sort()
        group = [seed]
        area = float(entries[seed]["area"])
        for distance, index in candidates:
            if len(group) >= max_size:
                break
            if distance <= max_radius or len(group) < min_size or area < target_area:
                group.append(index)
                area += float(entries[index]["area"])
        for index in group:
            unassigned.discard(index)
        groups.append(group)

    merged: list[list[int]] = []
    for group in groups:
        if len(group) >= min_size or not merged:
            merged.append(group)
            continue
        gx = sum(entries[index]["centroid"][0] for index in group) / len(group)
        gy = sum(entries[index]["centroid"][1] for index in group) / len(group)
        nearest = min(
            range(len(merged)),
            key=lambda target: math.hypot(
                gx - sum(entries[index]["centroid"][0] for index in merged[target]) / len(merged[target]),
                gy - sum(entries[index]["centroid"][1] for index in merged[target]) / len(merged[target]),
            ),
        )
        merged[nearest].extend(group)

    result = []
    for group_index, indices in enumerate(merged, start=1):
        group_entries = [entries[index] for index in indices]
        area = sum(float(entry["area"]) for entry in group_entries)
        if len(result) > 0 and area < min_viable_area:
            gx = sum(entry["centroid"][0] for entry in group_entries) / len(group_entries)
            gy = sum(entry["centroid"][1] for entry in group_entries) / len(group_entries)
            nearest = min(
                result,
                key=lambda item: math.hypot(gx - item["centroid"][0], gy - item["centroid"][1]),
            )
            nearest["entries"].extend(group_entries)
            nearest["region_ids"].extend(entry["region_id"] for entry in group_entries)
            nearest["area"] += area
            continue
        cx = sum(entry["centroid"][0] * float(entry["area"]) for entry in group_entries) / max(area, 1e-6)
        cy = sum(entry["centroid"][1] * float(entry["area"]) for entry in group_entries) / max(area, 1e-6)
        result.append(
            {
                "id": f"small_{group_index:03d}",
                "entries": group_entries,
                "region_ids": [entry["region_id"] for entry in group_entries],
                "area": area,
                "centroid": [cx, cy],
            }
        )
    return result


def choose_centers(
    entries: list[dict[str, Any]],
    count: int,
    rng: random.Random,
    edge_buffer: float,
    min_separation: float,
) -> list[dict[str, Any]]:
    weights = [max(1.0, float(entry["area"])) for entry in entries]
    centers: list[dict[str, Any]] = []
    for _ in range(count * 80):
        if len(centers) >= count:
            break
        entry = rng.choices(entries, weights=weights, k=1)[0]
        source = sample_valid_point_from_entry(entry, rng, edge_buffer, attempts=200)
        if not source:
            continue
        if any(math.hypot(source[0] - item["point"][0], source[1] - item["point"][1]) < min_separation for item in centers):
            continue
        radius = rng.uniform(22.0, 64.0)
        centers.append(
            {
                "point": source,
                "rx": radius * rng.uniform(1.2, 2.15),
                "ry": radius * rng.uniform(0.46, 0.9),
                "angle": rng.uniform(0.0, math.tau),
                "weight": rng.uniform(0.58, 0.95),
            }
        )
    while len(centers) < count:
        entry = rng.choices(entries, weights=weights, k=1)[0]
        source = sample_valid_point_from_entry(entry, rng, edge_buffer, attempts=400)
        if not source:
            break
        radius = rng.uniform(22.0, 64.0)
        centers.append(
            {
                "point": source,
                "rx": radius * rng.uniform(1.2, 2.15),
                "ry": radius * rng.uniform(0.46, 0.9),
                "angle": rng.uniform(0.0, math.tau),
                "weight": rng.uniform(0.58, 0.95),
            }
        )
    return centers


def grove_density_at(x: float, y: float, groves: list[dict[str, Any]], clearings: list[dict[str, Any]], base_density: float) -> float:
    density = base_density
    for grove in groves:
        cx, cy, _cz = grove["point"]
        angle = float(grove["angle"])
        dx = x - cx
        dy = y - cy
        local_x = dx * math.cos(angle) + dy * math.sin(angle)
        local_y = -dx * math.sin(angle) + dy * math.cos(angle)
        density += float(grove["weight"]) * math.exp(-0.5 * ((local_x / grove["rx"]) ** 2 + (local_y / grove["ry"]) ** 2))
    for clearing in clearings:
        cx, cy, _cz = clearing["point"]
        radius = max(1.0, float(clearing["radius"]))
        density -= float(clearing["weight"]) * math.exp(-0.5 * (((x - cx) / radius) ** 2 + ((y - cy) / radius) ** 2))
    return max(0.0, min(1.0, density))


def rotation_z_quaternion(degrees: float) -> list[float]:
    half_angle = math.radians(degrees) * 0.5
    return [0.0, 0.0, math.sin(half_angle), math.cos(half_angle)]


def make_point(source: list[float], entry: dict[str, Any], point_id: str, group_id: str, placement_role: str, rng: random.Random) -> dict[str, Any]:
    position = [float(source[0]), float(source[1]), float(source[2])]
    return {
        "id": point_id,
        "region_id": entry["region_id"],
        "role_key": "unassigned_role",
        "generated_position": position,
        "terrain_sample": None,
        "resolved_position": position.copy(),
        "rotation_quaternion_xyzw": rotation_z_quaternion(rng.uniform(0.0, 360.0)),
        "group_id": group_id,
        "_density_score": 0.82 if placement_role == "coverage" else 0.68,
    }


def generate_unit(
    entries: list[dict[str, Any]],
    group_id: str,
    target_count: int,
    rng: random.Random,
    start_index: int,
    center_count: int,
    coverage: bool,
    edge_buffer: float,
    min_point_distance: float,
    small_patch_min_viable_area: float,
    count_multiple: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    points: list[dict[str, Any]] = []
    grid: dict[tuple[int, int], list[int]] = {}
    covered_regions: set[str] = set()
    unplaceable_regions: list[str] = []
    cell_size = max(1.0, min_point_distance)

    def append_point(source: list[float], entry: dict[str, Any], role: str, min_distance: float) -> bool:
        if not is_far_enough_from_grid(source[0], source[1], points, grid, cell_size, min_distance):
            return False
        point = make_point(source, entry, f"plant_{start_index + len(points):05d}", group_id, role, rng)
        points.append(point)
        add_point_to_grid(point, len(points) - 1, grid, cell_size)
        covered_regions.add(entry["region_id"])
        return True

    if coverage:
        for entry in sorted(entries, key=lambda item: float(item["area"])):
            if float(entry["area"]) < small_patch_min_viable_area:
                unplaceable_regions.append(entry["region_id"])
                continue
            source = sample_valid_point_from_entry(entry, rng, edge_buffer, attempts=3000)
            if not source:
                unplaceable_regions.append(entry["region_id"])
                continue
            if not append_point(source, entry, "coverage", min_point_distance * 0.65):
                if not append_point(source, entry, "coverage", 0.25):
                    unplaceable_regions.append(entry["region_id"])

    target_count = round_to_multiple(max(target_count, len(points)), count_multiple)
    all_area = sum(float(entry["area"]) for entry in entries)
    min_separation = max(28.0, math.sqrt(max(all_area, 1.0) / max(center_count, 1)) * 0.45)
    centers = choose_centers(entries, max(1, center_count), rng, edge_buffer + 1.0, min_separation)
    clearings = choose_centers(entries, max(1, center_count // 2), rng, edge_buffer + 1.0, min_separation * 0.8)
    for clearing in clearings:
        clearing["radius"] = rng.uniform(35.0, 105.0)
        clearing["weight"] = rng.uniform(0.22, 0.48)

    weights = [max(1.0, float(entry["area"])) for entry in entries]
    attempts = 0
    max_attempts = max(5000, target_count * 900)
    while len(points) < target_count and attempts < max_attempts:
        attempts += 1
        entry = rng.choices(entries, weights=weights, k=1)[0]
        source = sample_valid_point_from_entry(entry, rng, edge_buffer, attempts=60)
        if not source:
            continue
        density = grove_density_at(source[0], source[1], centers, clearings, 0.025)
        density = max(0.0, min(1.0, density ** 1.12))
        if rng.random() > density:
            continue
        append_point(source, entry, "split_patch_grove", min_point_distance)

    relaxed_attempts = 0
    while len(points) < target_count and relaxed_attempts < target_count * 120:
        relaxed_attempts += 1
        entry = rng.choices(entries, weights=weights, k=1)[0]
        source = sample_valid_point_from_entry(entry, rng, edge_buffer, attempts=80)
        if source:
            append_point(source, entry, "split_patch_grove_relaxed", min(1.0, min_point_distance))

    summary = {
        "id": group_id,
        "target_point_count": target_count,
        "point_count": len(points),
        "region_count": len(entries),
        "region_ids": [entry["region_id"] for entry in entries],
        "area": all_area,
        "center_count": len(centers),
        "coverage_requested": coverage,
        "covered_region_count": len(covered_regions),
        "unplaceable_regions": sorted(set(unplaceable_regions)),
    }
    return points, summary


def weighted_role_counts(total: int, roles: list[dict[str, Any]]) -> list[int]:
    weights = [float(item.get("ratio", item.get("weight", 0.0))) for item in roles]
    total_weight = sum(weights) or 1.0
    exact = [(weight / total_weight) * total for weight in weights]
    counts = [math.floor(value) for value in exact]
    remainder = total - sum(counts)
    fractions = sorted(((exact[index] - counts[index], index) for index in range(len(exact))), reverse=True)
    for _fraction, index in fractions[:remainder]:
        counts[index] += 1
    return counts


def assign_roles_by_group(points: list[dict[str, Any]], strategy: dict[str, Any], seed: int) -> None:
    roles = list(strategy["role_mix"]["roles"])
    role_keys = [str(role.get("role_key", "")) for role in roles]
    role_weights = [float(role.get("ratio", role.get("weight", 0.0))) for role in roles]
    if not roles or any(not role_key for role_key in role_keys):
        raise ValueError("role_mix requires non-empty role_key values")
    if len(role_keys) != len(set(role_keys)):
        raise ValueError("role_mix role_key values must be unique")
    if any(weight < 0.0 for weight in role_weights) or sum(role_weights) <= 0.0:
        raise ValueError("role_mix requires non-negative ratios with a positive sum")
    rng = random.Random(seed + 104729)
    groups: dict[str, list[dict[str, Any]]] = {}
    for point in points:
        groups.setdefault(str(point.get("grove_group_id", "ungrouped")), []).append(point)

    for group_id, group_points in sorted(groups.items()):
        counts = weighted_role_counts(len(group_points), roles)
        ranked = sorted(group_points, key=lambda point: (float(point.get("_density_score", 0.0)), rng.random()), reverse=True)
        cursor = 0
        for role, count in zip(roles, counts):
            role_key = str(role["role_key"])
            for point in ranked[cursor : cursor + count]:
                point["role_key"] = role_key
            cursor += count
    for point in points:
        point.pop("_density_score", None)


def validate_points_against_entries(points: list[dict[str, Any]], entries: list[dict[str, Any]], edge_buffer: float) -> dict[str, Any]:
    by_region = {entry["region_id"]: entry for entry in entries}
    invalid = []
    min_distance: float | None = None
    for point in points:
        entry = by_region.get(str(point.get("region_id", "")))
        if not entry:
            invalid.append({"id": point.get("id"), "reason": "missing_region"})
            continue
        x = float(point["generated_position"][0])
        y = float(point["generated_position"][1])
        distance = distance_to_region_boundary_xy(x, y, entry["loops"])
        if point_in_region_with_margin(x, y, entry["loops"], edge_buffer):
            min_distance = distance if min_distance is None else min(min_distance, distance)
        else:
            invalid.append({"id": point.get("id"), "region_id": entry["region_id"], "distance": distance})
    return {
        "schema": "vegetation-mcp.planting-validation.v1",
        "checked_point_count": len(points),
        "invalid_count": len(invalid),
        "edge_buffer": edge_buffer,
        "min_accepted_boundary_distance": min_distance,
        "invalid_points_sample": invalid[:20],
    }


def generate_patch_grove_group_points(
    selected_regions: list[dict[str, Any]],
    strategy_override: dict[str, Any] | None = None,
    seed: int = 202607072130,
) -> dict[str, Any]:
    strategy = deep_merge(DEFAULT_STRATEGY, strategy_override)
    model = strategy["distribution_model"]
    density = model["density_defaults"]
    large_cfg = model["large_patch_strategy"]
    small_cfg = model["small_patch_strategy"]
    area_classes = model["area_classes"]

    rng = random.Random(seed)
    entries = region_sampling_entries(selected_regions)
    large_threshold = float(area_classes["large_patch_area_threshold"])
    target_density = float(density["target_density_per_area"])
    edge_buffer = float(density["edge_buffer"])
    large_entries = [entry for entry in entries if float(entry["area"]) >= large_threshold]
    small_entries = [entry for entry in entries if float(entry["area"]) < large_threshold]

    point_index = 1
    points: list[dict[str, Any]] = []

    for entry in sorted(large_entries, key=lambda item: float(item["area"]), reverse=True):
        area = float(entry["area"])
        subgrove_count = clamp(
            int(round(area / float(large_cfg["subgrove_area_factor"]))),
            int(large_cfg["subgrove_count_min"]),
            int(large_cfg["subgrove_count_max"]),
        )
        total_count = round_to_multiple(area * target_density, int(large_cfg["count_multiple"]))
        for sub_index, count in enumerate(distribute_counts(total_count, subgrove_count, int(large_cfg["count_multiple"])), start=1):
            group_id = f"large_{entry['region_id']}_{sub_index:02d}"
            unit_points, _summary = generate_unit(
                [entry],
                group_id,
                count,
                rng,
                point_index,
                int(large_cfg["center_count_per_subgrove"]),
                False,
                edge_buffer,
                float(density["large_patch_min_point_distance"]),
                float(area_classes["small_patch_min_viable_area"]),
                int(large_cfg["count_multiple"]),
            )
            point_index += len(unit_points)
            points.extend(unit_points)

    for group in build_adjacent_patch_groups(small_entries, strategy):
        group_entries = group["entries"]
        coverage_count = sum(1 for entry in group_entries if float(entry["area"]) >= float(area_classes["small_patch_min_viable_area"]))
        count = round_to_multiple(max(float(group["area"]) * target_density, coverage_count), int(small_cfg["count_multiple"]))
        center_count = clamp(
            int(round(float(group["area"]) / float(small_cfg["center_area_factor"]))),
            int(small_cfg["center_count_min"]),
            int(small_cfg["center_count_max"]),
        )
        unit_points, _summary = generate_unit(
            group_entries,
            group["id"],
            count,
            rng,
            point_index,
            center_count,
            True,
            edge_buffer,
            float(density["small_patch_min_point_distance"]),
            float(area_classes["small_patch_min_viable_area"]),
            int(small_cfg["count_multiple"]),
        )
        point_index += len(unit_points)
        points.extend(unit_points)

    assign_roles_by_group(points, strategy, seed)
    validation = validate_points_against_entries(points, entries, edge_buffer)
    if validation["invalid_count"]:
        raise ValueError(
            f"Generated points failed boundary validation: {validation['invalid_count']}"
        )
    return {
        "schema": "verdflow.points",
        "version": "2",
        "strategy": {
            "strategy_id": strategy["strategy_id"],
            "strategy_version": strategy["strategy_version"],
        },
        "coordinate_space": "source_scene_world",
        "offset_applied": False,
        "kind": "generated",
        "resolution_policy": "generated_position",
        "point_count": len(points),
        "points": points,
    }


def generate(context: dict[str, Any]) -> dict[str, Any]:
    """Generate a target-neutral PointSet from selected area regions."""
    args = dict(context.get("args") or {})
    strategy_document = dict(context.get("strategy") or {})
    parameters = dict(strategy_document.get("parameters") or {})
    strategy_override = {
        "strategy_id": strategy_document.get("strategy_id", DEFAULT_STRATEGY["strategy_id"]),
        "strategy_version": strategy_document.get("strategy_version", DEFAULT_STRATEGY["strategy_version"]),
        "role_mix": parameters.get("role_mix", DEFAULT_STRATEGY["role_mix"]),
        "distribution_model": parameters.get(
            "distribution_model", DEFAULT_STRATEGY["distribution_model"]
        ),
    }
    if isinstance(args.get("strategy_parameters"), dict):
        strategy_override["distribution_model"] = deep_merge(
            strategy_override["distribution_model"], args["strategy_parameters"]
        )
    return generate_patch_grove_group_points(
        list(context.get("selected_regions") or []),
        strategy_override=strategy_override,
        seed=int(context.get("seed", 202607072130)),
    )
