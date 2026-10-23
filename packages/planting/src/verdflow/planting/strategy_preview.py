"""Bounded strategy-owned synthetic plot declarations, with no UI semantics."""

from __future__ import annotations

import math
from typing import Any

from verdflow.contracts import StrategyPreviewSample

from .errors import PlantingDocumentError


def strategy_preview_sample(strategy: dict[str, Any]) -> StrategyPreviewSample | None:
    extensions = strategy.get("extensions", {})
    value = (
        extensions.get("parameter_preview") if isinstance(extensions, dict) else None
    )
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {
        "version",
        "display_name",
        "fixed_seed",
        "boundary_xy",
    }:
        raise PlantingDocumentError("Invalid parameter preview declaration")
    if value["version"] != "1":
        raise PlantingDocumentError("Unsupported parameter preview version")
    name = value["display_name"]
    if not isinstance(name, str) or not name.strip() or len(name) > 80:
        raise PlantingDocumentError("Invalid preview sample name")
    seed = value["fixed_seed"]
    if (
        isinstance(seed, bool)
        or not isinstance(seed, int)
        or not 0 <= seed <= 2**31 - 1
    ):
        raise PlantingDocumentError("Invalid preview fixed seed")
    boundary = value["boundary_xy"]
    if not isinstance(boundary, list) or not 3 <= len(boundary) <= 256:
        raise PlantingDocumentError("Preview boundary requires 3 to 256 vertices")
    points = []
    for point in boundary:
        if not isinstance(point, list) or len(point) != 2:
            raise PlantingDocumentError("Invalid preview boundary vertex")
        if any(
            isinstance(v, bool)
            or not isinstance(v, (float, int))
            or not math.isfinite(v)
            or abs(v) > 10000
            for v in point
        ):
            raise PlantingDocumentError(
                "Preview coordinates must be finite local metres"
            )
        points.append((float(point[0]), float(point[1])))
    xs, ys = zip(*points)
    if max(xs) - min(xs) > 1000 or max(ys) - min(ys) > 1000:
        raise PlantingDocumentError("Preview extent exceeds 1000 metres")
    area = (
        abs(
            sum(
                a[0] * b[1] - b[0] * a[1]
                for a, b in zip(points, points[1:] + points[:1])
            )
        )
        / 2
    )
    if not 0.01 <= area <= 50000:
        raise PlantingDocumentError(
            "Preview area must be positive and at most 50000 square metres"
        )
    return StrategyPreviewSample(name.strip(), tuple(points), seed)
