"""Package-owned display guides, interpreted only by query projections."""

from __future__ import annotations

from dataclasses import replace
import math
from typing import Any

from .models import StrategyRoleDiagramMarkInfo, StrategyRoleGuideInfo, StrategyRoleInfo


def present_strategy_roles(
    document: dict[str, Any],
    roles: tuple[StrategyRoleInfo, ...],
) -> tuple[StrategyRoleInfo, ...]:
    extensions = document.get("extensions", {})
    supplied = extensions.get("role_presentation") if isinstance(extensions, dict) else None
    if supplied is None:
        return roles
    try:
        return _present(supplied, roles)
    except (KeyError, TypeError, ValueError):
        # Invalid optional author metadata must not block the usable strategy.
        return roles


def _present(value: object, roles: tuple[StrategyRoleInfo, ...]) -> tuple[StrategyRoleInfo, ...]:
    if not isinstance(value, dict) or value.get("version") != "1":
        raise ValueError("Unsupported role presentation")
    labels = value["roles"]
    if not isinstance(labels, dict) or set(labels) != {role.role_key for role in roles}:
        raise ValueError("Role guide must describe the exact role set")
    groups = value["groups"]
    if not isinstance(groups, list) or not 1 <= len(groups) <= 32:
        raise ValueError("Invalid role groups")
    by_role: dict[str, tuple[dict[str, Any], tuple[StrategyRoleDiagramMarkInfo, ...]]] = {}
    group_ids: set[str] = set()
    for group_index, group in enumerate(groups):
        group_id = _text(group["id"])
        if group_id in group_ids:
            raise ValueError("Duplicate role group")
        group_ids.add(group_id)
        order = group.get("order", group_index)
        if isinstance(order, bool) or not isinstance(order, int) or not 0 <= order <= 32:
            raise ValueError("Invalid role group order")
        _text(group["name"])
        _text(group.get("description", ""), allow_empty=True)
        members = group["role_keys"]
        if not isinstance(members, list) or not members:
            raise ValueError("Empty role group")
        raw_marks = group.get("diagram", [])
        if not isinstance(raw_marks, list) or len(raw_marks) > 128:
            raise ValueError("Invalid role diagram")
        marks = tuple(_mark(mark, set(labels)) for mark in raw_marks)
        for role_key in members:
            if role_key not in labels or role_key in by_role:
                raise ValueError("Unknown or duplicated role")
            by_role[role_key] = (group, marks)
    if set(by_role) != set(labels):
        raise ValueError("Role grouping is incomplete")
    presented = []
    for role in roles:
        label = labels[role.role_key]
        if not isinstance(label, dict):
            raise ValueError("Invalid role guide label")
        group, marks = by_role[role.role_key]
        suggested = label.get("suggested_assets", [])
        if not isinstance(suggested, list) or len(suggested) > 8:
            raise ValueError("Invalid asset suggestions")
        presented.append(replace(
            role,
            guide=StrategyRoleGuideInfo(
                group_id=group["id"], group_order=group.get("order", groups.index(group)),
                group_name=group["name"],
                group_description=_text(group.get("description", ""), allow_empty=True),
                suggested_assets=tuple(_text(item) for item in suggested),
                note=_text(label.get("note", ""), allow_empty=True), diagram=marks,
            ),
        ))
    return tuple(sorted(presented, key=lambda role: role.guide.group_order))


def _mark(value: object, role_keys: set[str]) -> StrategyRoleDiagramMarkInfo:
    if not isinstance(value, dict) or value.get("role_key") not in role_keys:
        raise ValueError("Diagram references an unknown role")
    kind = value["kind"]
    if kind not in {"area", "tree", "shrub", "groundcover"}:
        raise ValueError("Unsupported diagram mark")
    raw_points = value.get("points", [])
    if not isinstance(raw_points, list) or len(raw_points) > 32:
        raise ValueError("Invalid diagram boundary")
    points = []
    for point in raw_points:
        if not isinstance(point, list) or len(point) != 2:
            raise ValueError("Invalid diagram vertex")
        points.append((_number(point[0], 100), _number(point[1], 62)))
    if kind == "area" and len(points) < 3:
        raise ValueError("Diagram area requires a boundary")
    return StrategyRoleDiagramMarkInfo(
        role_key=value["role_key"], kind=kind,
        x=_number(value.get("x", 0), 100), y=_number(value.get("y", 0), 62),
        size=_number(value.get("size", 6), 24), points=tuple(points),
    )


def _text(value: object, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > 500 or (not allow_empty and not value.strip()):
        raise ValueError("Invalid role guide text")
    return value


def _number(value: object, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Invalid diagram coordinate")
    if not 0 <= value <= maximum:
        raise ValueError("Diagram coordinate out of range")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Invalid diagram coordinate")
    return number
