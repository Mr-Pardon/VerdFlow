"""Deterministic algorithms that do not depend on Blender."""

from __future__ import annotations

import math
from collections import defaultdict, deque
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any


Edge = tuple[int, int]
Point3 = tuple[float, float, float]


def logical_texture_path(
    absolute_path: str,
    source_roots: Sequence[Path],
) -> tuple[str, str]:
    if not absolute_path.strip():
        return "", "missing"
    absolute = Path(absolute_path).resolve(strict=False)
    for root in sorted(
        (value.resolve(strict=False) for value in source_roots),
        key=lambda value: len(value.parts),
        reverse=True,
    ):
        try:
            relative = absolute.relative_to(root)
        except ValueError:
            continue
        return relative.as_posix(), "source_relative"
    return absolute.name, "external_filename_only"


def sanitize_name(value: str | None) -> str:
    text = (value or "region").strip().replace(" ", "_")
    normalized = "".join(
        character if character.isalnum() or character in "_-" else "_"
        for character in text
    )
    return normalized[:48] or "region"


def polygon_area_xy(points: Sequence[Point3]) -> float:
    return abs(signed_polygon_area_xy(points))


def signed_polygon_area_xy(points: Sequence[Point3]) -> float:
    if len(points) < 3:
        return 0.0
    origin_x, origin_y = points[0][0], points[0][1]
    area = math.fsum(
        (point[0] - origin_x) * (next_point[1] - origin_y)
        - (next_point[0] - origin_x) * (point[1] - origin_y)
        for point, next_point in zip(points, points[1:] + points[:1])
    )
    return area * 0.5


def polygon_perimeter_xy(points: Sequence[Point3]) -> float:
    if len(points) < 2:
        return 0.0
    total = 0.0
    for index, point in enumerate(points):
        next_point = points[(index + 1) % len(points)]
        total += math.hypot(
            point[0] - next_point[0],
            point[1] - next_point[1],
        )
    return total


def connected_face_islands(
    face_edges: Mapping[int, Iterable[Edge]],
) -> list[list[int]]:
    edge_to_faces: dict[Edge, list[int]] = defaultdict(list)
    for face_index, edges in face_edges.items():
        for edge in edges:
            edge_to_faces[tuple(sorted(edge))].append(face_index)

    neighbors: dict[int, set[int]] = defaultdict(set)
    for faces in edge_to_faces.values():
        for face in faces:
            neighbors[face].update(
                other for other in faces if other != face
            )

    islands: list[list[int]] = []
    remaining = set(face_edges)
    while remaining:
        start = min(remaining)
        remaining.remove(start)
        island = {start}
        queue = deque([start])
        while queue:
            current = queue.popleft()
            for next_face in sorted(neighbors[current]):
                if next_face not in remaining:
                    continue
                remaining.remove(next_face)
                island.add(next_face)
                queue.append(next_face)
        islands.append(sorted(island))
    return islands


def boundary_vertex_loops(
    face_edges: Iterable[Iterable[Edge]],
) -> list[list[int]]:
    edge_counts: dict[Edge, int] = defaultdict(int)
    for edges in face_edges:
        for edge in edges:
            edge_counts[tuple(sorted(edge))] += 1
    boundary_edges = sorted(
        edge for edge, count in edge_counts.items() if count == 1
    )
    adjacency: dict[int, set[int]] = defaultdict(set)
    for first, second in boundary_edges:
        adjacency[first].add(second)
        adjacency[second].add(first)

    loops: list[list[int]] = []
    visited: set[Edge] = set()
    for first, second in boundary_edges:
        edge = tuple(sorted((first, second)))
        if edge in visited:
            continue
        loop = [first, second]
        visited.add(edge)
        previous = first
        current = second
        while True:
            next_vertex = next(
                (
                    candidate
                    for candidate in sorted(adjacency[current])
                    if candidate != previous
                    and tuple(sorted((current, candidate))) not in visited
                ),
                None,
            )
            if next_vertex is None:
                break
            visited.add(tuple(sorted((current, next_vertex))))
            if next_vertex == loop[0]:
                break
            loop.append(next_vertex)
            previous, current = current, next_vertex
        if len(loop) >= 3:
            loops.append(loop)
    return loops


def dominant_projected_boundary_loops(
    face_edges: Mapping[int, Iterable[Edge]],
    face_points: Mapping[int, Sequence[Point3]],
    *,
    area_epsilon: float = 1e-9,
) -> list[list[int]]:
    """Return the XY outline of the dominant upward/downward face layer."""
    signed_areas = {
        face_index: signed_polygon_area_xy(face_points.get(face_index, ()))
        for face_index in face_edges
    }
    positive_area = math.fsum(
        area for area in signed_areas.values() if area > area_epsilon
    )
    negative_area = math.fsum(
        -area for area in signed_areas.values() if area < -area_epsilon
    )
    if positive_area <= area_epsilon and negative_area <= area_epsilon:
        return []
    sign = 1.0 if positive_area >= negative_area else -1.0
    return boundary_vertex_loops(
        face_edges[face_index]
        for face_index in sorted(face_edges)
        if signed_areas[face_index] * sign > area_epsilon
    )


def validate_rule_pack(
    rule_pack: Mapping[str, Any],
    *,
    allow_contains: bool = False,
) -> None:
    rules = rule_pack.get("rules")
    if not isinstance(rules, list) or not rules:
        raise ValueError("rule pack requires at least one rule")
    seen: set[str] = set()
    contains_keys = {
        "material_name_contains",
        "texture_path_contains",
        "text_contains",
    }
    supported_keys = contains_keys | {"material_names", "object_names"}
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise ValueError(f"rule {index} must be an object")
        rule_id = str(rule.get("id", "")).strip()
        if not rule_id:
            raise ValueError(f"rule {index} requires id")
        if rule_id in seen:
            raise ValueError(f"duplicate rule id: {rule_id}")
        seen.add(rule_id)
        match = rule.get("match")
        if not isinstance(match, dict):
            raise ValueError(f"rule {rule_id} requires match")
        present = {
            key for key in supported_keys if _string_values(match.get(key))
        }
        if not present:
            raise ValueError(f"rule {rule_id} has no supported match values")
        if not allow_contains and present & contains_keys:
            raise ValueError(
                f"rule {rule_id} uses contains matching without permission"
            )


def rule_match_evidence(
    rule: Mapping[str, Any],
    *,
    material_name: str,
    object_name: str,
    texture_values: Sequence[str],
) -> list[str]:
    match = rule.get("match", {})
    exact_materials = set(_string_values(match.get("material_names")))
    if material_name in exact_materials:
        return [f"material_name:{material_name}"]
    exact_objects = set(_string_values(match.get("object_names")))
    if object_name in exact_objects:
        return [f"object_name:{object_name}"]

    texture_blob = " ".join(texture_values).casefold()
    material_folded = material_name.casefold()
    all_text = f"{object_name} {material_name} {texture_blob}".casefold()
    evidence: list[str] = []
    for token in _string_values(match.get("material_name_contains")):
        if token.casefold() in material_folded:
            evidence.append(f"material_name_contains:{token}")
    for token in _string_values(match.get("texture_path_contains")):
        if token.casefold() in texture_blob:
            evidence.append(f"texture_path_contains:{token}")
    for token in _string_values(match.get("text_contains")):
        if token.casefold() in all_text:
            evidence.append(f"text_contains:{token}")
    return evidence


def first_matching_rule(
    rules: Sequence[Mapping[str, Any]],
    *,
    material_name: str,
    object_name: str,
    texture_values: Sequence[str],
) -> tuple[str, list[str]] | None:
    """Resolve the first matching rule while preserving rule-pack order."""
    for rule in rules:
        evidence = rule_match_evidence(
            rule,
            material_name=material_name,
            object_name=object_name,
            texture_values=texture_values,
        )
        if evidence:
            return str(rule["id"]), evidence
    return None


def _string_values(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
