"""Decode bounded display assignment input, never strategy or asset dimensions."""

from typing import Any

from .assignment_previews import (
    AssignmentPreviewCandidate,
    AssignmentPreviewRequest,
    AssignmentPreviewRole,
)
from .strategy_previews import StrategyPreviewRequest


def parse_assignment_preview_request(
    document: dict[str, Any],
) -> AssignmentPreviewRequest:
    if set(document) != {"random_seed", "points", "roles"}:
        raise ValueError("Invalid assignment preview fields")
    points, roles = document["points"], document["roles"]
    if (
        not isinstance(points, list)
        or len(points) > 100000
        or not isinstance(roles, list)
        or len(roles) > 128
    ):
        raise ValueError("Assignment preview exceeds its budget")
    if any(not isinstance(p, list) or len(p) != 2 for p in points):
        raise ValueError("Invalid assignment preview points")
    parsed = []
    for role in roles:
        if not isinstance(role, dict) or set(role) != {"role_key", "candidates"}:
            raise ValueError("Invalid assignment preview role")
        candidates = role["candidates"]
        if not isinstance(candidates, list) or len(candidates) > 100:
            raise ValueError("Invalid assignment preview candidates")
        values = []
        for candidate in candidates:
            if not isinstance(candidate, dict) or set(candidate) != {
                "asset_id",
                "weight",
                "scale",
            }:
                raise ValueError("Invalid assignment preview candidate")
            scale = candidate["scale"]
            if not isinstance(scale, dict):
                raise ValueError("Invalid assignment preview scale")
            if scale.get("mode") == "fixed" and set(scale) == {"mode", "value"}:
                minimum = maximum = scale["value"]
            elif scale.get("mode") == "uniform_random" and set(scale) == {
                "mode",
                "min",
                "max",
            }:
                minimum, maximum = scale["min"], scale["max"]
            else:
                raise ValueError("Invalid assignment preview scale")
            values.append(
                AssignmentPreviewCandidate(
                    candidate["asset_id"],
                    candidate["weight"],
                    scale["mode"],
                    minimum,
                    maximum,
                )
            )
        parsed.append(AssignmentPreviewRole(role["role_key"], tuple(values)))
    return AssignmentPreviewRequest(
        document["random_seed"], tuple(tuple(p) for p in points), tuple(parsed)
    )


def parse_strategy_preview_request(document: dict[str, Any]) -> StrategyPreviewRequest:
    if set(document) - {"package_hash", "strategy_parameters", "excluded_role_keys"}:
        raise ValueError("Unknown strategy example preview field")
    package_hash = document.get("package_hash")
    values = document.get("strategy_parameters", {})
    excluded = document.get("excluded_role_keys", [])
    if not isinstance(package_hash, str) or not package_hash.strip():
        raise ValueError("package_hash is required")
    if not isinstance(values, dict) or len(values) > 128:
        raise ValueError("strategy_parameters must be a bounded object")
    if (
        not isinstance(excluded, list)
        or len(excluded) > 128
        or any(not isinstance(role, str) for role in excluded)
    ):
        raise ValueError("excluded_role_keys must be a string array")
    return StrategyPreviewRequest(package_hash, values, tuple(excluded))
