"""Deterministic target asset and scale resolution."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from typing import Any, BinaryIO

from verdflow.contracts import AssignmentPreviewRequest, AssignmentPreviewResult

from .canonical import CanonicalRowHasher, canonical_array_hash, canonical_row_bytes, point_set_row_hasher
from .errors import PlantingDocumentError
from .point_stream import PointSetReader
from .validation import (
    validate_assignment_set,
    validate_effective_binding_snapshot,
    validate_persisted_point_set_summary,
    validate_point_header,
    validate_point_set,
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

    point_set = validate_point_set(point_set)
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
        resolved.append(_resolve_row(point, snapshot, random_seed))
        if index % _PROGRESS_INTERVAL == 0 or index == point_count:
            _report_progress(
                progress,
                "resolving_asset_assignments",
                index / point_count,
            )

    payload = _assignment_header(
        snapshot, random_seed, point_count, canonical_array_hash(point_set, "points"),
    )
    payload["assignments"] = resolved
    _report_progress(progress, "validating_asset_assignments", 0.0)
    payload["semantic_hash"] = canonical_array_hash(payload, "assignments")
    validated = validate_assignment_set(payload)
    _report_progress(progress, "validating_asset_assignments", 1.0)
    return validated


def write_persisted_asset_assignments(
    source: BinaryIO,
    destination: BinaryIO,
    effective_binding_snapshot: dict[str, Any],
    *,
    random_seed: int,
    expected_point_count: int,
    point_kind: str,
    progress: AssignmentProgressCallback | None = None,
) -> None:
    """Write canonical AssignmentSet bytes; the caller stages output until success."""
    if type(random_seed) is not int or not 0 <= random_seed <= MAX_SEED:
        raise PlantingDocumentError(f"random_seed must be between 0 and {MAX_SEED}")
    snapshot = validate_effective_binding_snapshot(effective_binding_snapshot)
    points = PointSetReader(source, expected_point_count=expected_point_count)
    point_hash = point_set_row_hasher(kind=point_kind, count=expected_point_count)
    assignment_hash = CanonicalRowHasher("assignments", {})
    _report_progress(progress, "resolving_asset_assignments", 0.0)
    destination.write(assignment_hash.prefix)
    for index, point in enumerate(points.rows(), start=1):
        point_hash.row(point)
        row = _resolve_row(point, snapshot, random_seed)
        destination.write(assignment_hash.row(row))
        if index % _PROGRESS_INTERVAL == 0 or index == expected_point_count:
            _report_progress(progress, "resolving_asset_assignments", index / expected_point_count)
    _report_progress(progress, "validating_asset_assignments", 0.0)
    header = points.header
    validate_point_header(header)
    if header["strategy"] != snapshot["strategy"]:
        raise PlantingDocumentError("PointSet strategy does not match EffectiveBindingSnapshot")
    assignment_header = _assignment_header(snapshot, random_seed, expected_point_count, point_hash.finish(header))
    assignment_header["semantic_hash"] = assignment_hash.finish(assignment_header)
    destination.write(assignment_hash.suffix(assignment_header))
    _report_progress(progress, "validating_asset_assignments", 1.0)


def _assignment_header(snapshot: dict[str, Any], seed: int, count: int, point_hash: str) -> dict[str, Any]:
    return {
        "schema": "verdflow.asset-assignment-set",
        "version": "1",
        "target_type": snapshot["target"]["target_type"],
        "resolver": {"id": RESOLVER_ID, "version": RESOLVER_VERSION},
        "random_seed": seed,
        "point_set_hash": point_hash,
        "effective_binding_snapshot_hash": snapshot["semantic_hash"],
        "point_count": count,
    }


def _resolve_row(point: dict[str, Any], snapshot: dict[str, Any], seed: int) -> dict[str, Any]:
    role_key = point["role_key"]
    point_id = point["id"]
    role = snapshot["assignments"].get(role_key)
    if role is None:
        raise PlantingDocumentError(f"point {point_id!r} uses unmapped role {role_key!r}")
    candidate = _choose_candidate(role["candidates"], seed, point_id)
    scale = _resolve_scale(candidate["scale"], seed, point_id)
    if not math.isfinite(scale) or scale <= 0.0:
        raise PlantingDocumentError("Resolved asset scale must be finite and positive")
    assignment = {
        "point_id": point_id, "role_key": role_key, "asset_id": candidate["asset_id"],
        "target_scale_xyz": [scale, scale, scale],
    }
    if point.get("group_id"):
        assignment["assignment_group_id"] = point["group_id"]
    return assignment


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
    if len(candidates) == 1:
        return candidates[0]
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
    material = canonical_row_bytes(
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
