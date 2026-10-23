"""Deterministic target asset and scale resolution."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from typing import Any

from verdflow.contracts import AssignmentPreviewRequest, AssignmentPreviewResult

from .canonical import canonical_json_bytes, semantic_hash
from .errors import PlantingDocumentError
from .validation import (
    validate_assignment_set,
    validate_effective_binding_snapshot,
    validate_persisted_point_set_summary,
    validate_point_set_v2,
    with_semantic_hash,
)


RESOLVER_ID = "verdflow.hash-weighted-assignment"
RESOLVER_VERSION = "1"
MAX_SEED = 2**63 - 1
_PROGRESS_INTERVAL = 2048

AssignmentProgressCallback = Callable[[str, float], None]


def resolve_assignment_preview(
    request: AssignmentPreviewRequest,
) -> AssignmentPreviewResult:
    """Reuse formal choice/scale rules; do not fabricate a PointSet or persist facts."""
    if type(request.random_seed) is not int or not 0 <= request.random_seed <= MAX_SEED:
        raise PlantingDocumentError("Invalid assignment preview seed")
    if len(request.points) > 100000 or len(request.roles) > 128:
        raise PlantingDocumentError("Assignment preview exceeds its budget")
    roles: dict[str, list[dict[str, Any]]] = {}
    for role in request.roles:
        if (
            not _preview_text(role.role_key, 128)
            or role.role_key in roles
            or len(role.candidates) > 100
        ):
            raise PlantingDocumentError("Invalid assignment preview role")
        candidates = []
        seen = set()
        for candidate in role.candidates:
            if (
                not _preview_text(candidate.asset_id, 256)
                or candidate.asset_id in seen
                or candidate.scale_mode not in {"fixed", "uniform_random"}
                or any(
                    type(v) not in (float, int) or not math.isfinite(v) or v <= 0
                    for v in (
                        candidate.weight,
                        candidate.scale_min,
                        candidate.scale_max,
                    )
                )
                or candidate.scale_min > candidate.scale_max
                or candidate.scale_max > 1000000
                or (
                    candidate.scale_mode == "fixed"
                    and candidate.scale_min != candidate.scale_max
                )
            ):
                raise PlantingDocumentError("Invalid assignment preview candidate")
            seen.add(candidate.asset_id)
            candidates.append(
                {
                    "asset_id": candidate.asset_id,
                    "weight": candidate.weight,
                    "scale": (
                        {"mode": "fixed", "value": candidate.scale_min}
                        if candidate.scale_mode == "fixed"
                        else {
                            "mode": "uniform_random",
                            "min": candidate.scale_min,
                            "max": candidate.scale_max,
                        }
                    ),
                }
            )
        roles[role.role_key] = candidates
    choices = []
    seen_points = set()
    for point_id, role_key in request.points:
        if (
            not _preview_text(point_id, 256)
            or point_id in seen_points
            or not _preview_text(role_key, 128)
        ):
            raise PlantingDocumentError("Invalid assignment preview point")
        seen_points.add(point_id)
        candidates = roles.get(role_key, [])
        if not candidates:
            choices.append((-1, 1.0))
            continue
        candidate = _choose_candidate(candidates, request.random_seed, point_id)
        choices.append(
            (
                candidates.index(candidate),
                _resolve_scale(candidate["scale"], request.random_seed, point_id),
            )
        )
    return AssignmentPreviewResult(tuple(choices))


def _preview_text(value: object, limit: int) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


def resolve_asset_assignments(
    point_set: dict[str, Any],
    effective_binding_snapshot: dict[str, Any],
    *,
    random_seed: int,
    progress: AssignmentProgressCallback | None = None,
) -> dict[str, Any]:
    """Resolve every point without relying on process-global random state."""

    point_set = validate_point_set_v2(point_set)
    snapshot = validate_effective_binding_snapshot(effective_binding_snapshot)
    return _resolve_validated_asset_assignments(
        point_set,
        snapshot,
        random_seed=random_seed,
        progress=progress,
    )


def resolve_persisted_asset_assignments(
    point_set: dict[str, Any],
    effective_binding_snapshot: dict[str, Any],
    *,
    random_seed: int,
    expected_point_count: int,
    progress: AssignmentProgressCallback | None = None,
) -> dict[str, Any]:
    """Resolve a PointSet already validated and frozen by VerdFlow Core."""

    point_set = validate_persisted_point_set_summary(
        point_set,
        expected_point_count=expected_point_count,
    )
    snapshot = validate_effective_binding_snapshot(effective_binding_snapshot)
    return _resolve_validated_asset_assignments(
        point_set,
        snapshot,
        random_seed=random_seed,
        progress=progress,
    )


def _resolve_validated_asset_assignments(
    point_set: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    random_seed: int,
    progress: AssignmentProgressCallback | None,
) -> dict[str, Any]:
    if not isinstance(random_seed, int) or isinstance(random_seed, bool):
        raise PlantingDocumentError("random_seed must be an integer")
    if random_seed < 0 or random_seed > MAX_SEED:
        raise PlantingDocumentError(f"random_seed must be between 0 and {MAX_SEED}")
    if point_set["strategy"] != snapshot["strategy"]:
        raise PlantingDocumentError(
            "PointSet strategy does not match EffectiveBindingSnapshot"
        )

    points = point_set["points"]
    point_count = len(points)
    _report_progress(progress, "resolving_asset_assignments", 0.0)
    resolved: list[dict[str, Any]] = []
    for index, point in enumerate(points, start=1):
        role_key = point["role_key"]
        role_assignment = snapshot["assignments"].get(role_key)
        if role_assignment is None:
            raise PlantingDocumentError(
                f"point {point['id']!r} uses unmapped role {role_key!r}"
            )
        candidate = _choose_candidate(
            role_assignment["candidates"],
            random_seed,
            point["id"],
        )
        scale = _resolve_scale(
            candidate["scale"],
            random_seed,
            point["id"],
        )
        assignment = {
            "point_id": point["id"],
            "role_key": role_key,
            "asset_id": candidate["asset_id"],
            "target_scale_xyz": [scale, scale, scale],
        }
        if point.get("group_id"):
            assignment["assignment_group_id"] = point["group_id"]
        resolved.append(assignment)
        if index % _PROGRESS_INTERVAL == 0 or index == point_count:
            _report_progress(
                progress,
                "resolving_asset_assignments",
                index / point_count,
            )

    payload = {
        "schema": "verdflow.asset-assignment-set",
        "version": "1",
        "target_type": snapshot["target"]["target_type"],
        "resolver": {"id": RESOLVER_ID, "version": RESOLVER_VERSION},
        "random_seed": random_seed,
        "point_set_hash": semantic_hash(point_set),
        "effective_binding_snapshot_hash": snapshot["semantic_hash"],
        "point_count": len(resolved),
        "assignments": resolved,
    }
    _report_progress(progress, "validating_asset_assignments", 0.0)
    validated = validate_assignment_set(with_semantic_hash(payload))
    _report_progress(progress, "validating_asset_assignments", 1.0)
    return validated


def _report_progress(
    callback: AssignmentProgressCallback | None,
    stage: str,
    progress: float,
) -> None:
    if callback is not None:
        callback(stage, progress)


def _choose_candidate(
    candidates: list[dict[str, Any]],
    seed: int,
    point_id: str,
) -> dict[str, Any]:
    weights = [Decimal(str(item["weight"])) for item in candidates]
    total = sum(weights, start=Decimal(0))
    with localcontext() as context:
        context.prec = 80
        threshold = (
            Decimal(_unit_integer(seed, point_id, "asset-choice-v1"))
            * total
            / Decimal(2**64)
        )
    cumulative = Decimal(0)
    for candidate, weight in zip(candidates, weights, strict=True):
        cumulative += weight
        if threshold < cumulative:
            return candidate
    return candidates[-1]


def _resolve_scale(policy: dict[str, Any], seed: int, point_id: str) -> float:
    if policy["mode"] == "fixed":
        return _round_scale(Decimal(str(policy["value"])))
    minimum = Decimal(str(policy["min"]))
    maximum = Decimal(str(policy["max"]))
    with localcontext() as context:
        context.prec = 80
        unit = Decimal(_unit_integer(seed, point_id, "uniform-scale-v1")) / Decimal(
            2**64
        )
        value = minimum + (maximum - minimum) * unit
    return _round_scale(value)


def _unit_integer(seed: int, point_id: str, purpose: str) -> int:
    material = canonical_json_bytes(
        {
            "resolver": RESOLVER_ID,
            "version": RESOLVER_VERSION,
            "purpose": purpose,
            "seed": seed,
            "point_id": point_id,
        }
    )
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big")


def _round_scale(value: Decimal) -> float:
    rounded = value.quantize(Decimal("0.000000001"), rounding=ROUND_HALF_EVEN)
    return float(rounded)
