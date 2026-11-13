"""Pure DTS ModelPoint XML rendering from immutable VerdFlow documents."""

from __future__ import annotations

import math
from dataclasses import dataclass
from io import BytesIO
from typing import Any, BinaryIO, Callable, Iterable
from xml.sax.saxutils import escape

from verdflow.planting import (
    validate_assignment_set,
    validate_effective_binding_snapshot,
    validate_point_set,
)

_XML_HEADER = b'<?xml version="1.0" encoding="UTF-8"?>\n<ModelPointClass>\n'
_XML_END = b"</ModelPointClass>\n"


@dataclass(frozen=True, slots=True)
class DtsXmlDocument:
    content: bytes
    record_count: int


def render_dts_xml(
    point_set: dict[str, Any],
    effective_binding: dict[str, Any],
    assignment_set: dict[str, Any],
    offset: tuple[float, float, float],
    progress: Callable[[int, int], None] | None = None,
) -> DtsXmlDocument:
    """Render one deterministic DTS XML document without changing inputs."""

    return render_dts_xml_composition(
        ((point_set, effective_binding, assignment_set),),
        offset,
        progress=progress,
    )


def render_dts_xml_composition(
    inputs: tuple[
        tuple[dict[str, Any], dict[str, Any], dict[str, Any]], ...
    ],
    offset: tuple[float, float, float],
    progress: Callable[[int, int], None] | None = None,
) -> DtsXmlDocument:
    """Render ordered immutable batch inputs into one DTS XML document."""

    validated = tuple(
        (
            validate_point_set(points),
            validate_effective_binding_snapshot(binding),
            validate_assignment_set(assignments),
        )
        for points, binding, assignments in inputs
    )
    return _render_persisted_dts_xml_composition(validated, offset, progress=progress)


def _render_persisted_dts_xml_composition(
    inputs: tuple[tuple[dict[str, Any], dict[str, Any], dict[str, Any]], ...],
    offset: tuple[float, float, float],
    progress: Callable[[int, int], None] | None = None,
) -> DtsXmlDocument:
    output = BytesIO()
    record_count = _write_persisted_dts_xml_composition(
        output, inputs, offset, total_count=sum(item[0]["point_count"] for item in inputs),
        progress=progress,
    )
    return DtsXmlDocument(content=output.getvalue(), record_count=record_count)


def _write_persisted_dts_xml_composition(
    output: BinaryIO,
    inputs: Iterable[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]],
    offset: tuple[float, float, float],
    *,
    total_count: int,
    progress: Callable[[int, int], None] | None = None,
) -> int:
    """Write one document while holding only the current batch of inputs."""
    if len(offset) != 3:
        raise ValueError("DTS offset must contain three coordinates")
    normalized_offset = _finite_vector(offset, "offset")
    output.write(_XML_HEADER)
    record_count = 0
    has_input = False
    for batch in inputs:
        has_input = True
        record_count += _append_points(
            output,
            *batch,
            normalized_offset,
            completed_count=record_count,
            total_count=total_count,
            progress=progress,
        )
        del batch
    if not has_input:
        raise ValueError("DTS composition export requires at least one input")
    output.write(_XML_END)
    return record_count


def _write_streamed_dts_xml(
    output: BinaryIO,
    inputs: Iterable[tuple[dict[str, Any], Iterable[tuple[dict[str, Any], dict[str, Any]]]]],
    offset: tuple[float, float, float],
) -> int:
    if len(offset) != 3:
        raise ValueError("DTS offset must contain three coordinates")
    normalized_offset = _finite_vector(offset, "offset")
    output.write(_XML_HEADER)
    has_input = False
    count = 0
    for binding, rows in inputs:
        has_input = True
        models = _model_names(binding)
        for point, assignment in rows:
            _write_model_point(output, point, assignment, models, normalized_offset)
            count += 1
    if not has_input:
        raise ValueError("DTS composition export requires at least one input")
    output.write(_XML_END)
    return count


def _append_points(
    output: BinaryIO,
    point_set: dict[str, Any],
    effective_binding: dict[str, Any],
    assignment_set: dict[str, Any],
    offset: tuple[float, float, float],
    *,
    completed_count: int,
    total_count: int,
    progress: Callable[[int, int], None] | None,
) -> int:

    points, binding, assignments = point_set, effective_binding, assignment_set
    if points["kind"] != "elevation_resolved":
        raise ValueError("DTS export requires an elevation_resolved PointSet")
    if (
        points.get("offset_applied") is not False
        or points.get("coordinate_space") != "source_scene_world"
    ):
        raise ValueError("DTS export requires unshifted source coordinates")
    if (
        points["point_count"] != len(points["points"])
        or assignments["point_count"] != len(assignments["assignments"])
    ):
        raise ValueError("DTS export input counts are inconsistent")
    if binding["target"]["target_type"] != "dts":
        raise ValueError("DTS export requires a dts effective binding")
    if assignments["target_type"] != "dts":
        raise ValueError("DTS export requires dts asset assignments")
    models = _model_names(binding)
    by_point: dict[str, dict[str, Any]] = {}
    for assignment in assignments["assignments"]:
        point_id = assignment["point_id"]
        if point_id in by_point:
            raise ValueError(f"Duplicate asset assignment for point: {point_id}")
        by_point[point_id] = assignment
    point_ids = {point["id"] for point in points["points"]}
    if len(point_ids) != len(points["points"]) or set(by_point) != point_ids:
        raise ValueError("DTS export requires exactly one assignment per point")

    for index, point in enumerate(points["points"], start=1):
        _write_model_point(output, point, by_point[point["id"]], models, offset)
        if progress is not None and (
            index % 5000 == 0 or index == len(points["points"])
        ):
            progress(completed_count + index, total_count)
    return len(points["points"])


def _write_model_point(
    output: BinaryIO, point: dict[str, Any], assignment: dict[str, Any],
    models: dict[str, str], offset: tuple[float, float, float],
) -> None:
    asset_id = assignment["asset_id"]
    model_name = models.get(asset_id)
    if model_name is None:
        raise ValueError(f"DTS model_name is missing for asset: {asset_id}")
    position = _finite_vector(tuple(point["resolved_position"]), "resolved_position")
    if len(position) != 3:
        raise ValueError("DTS resolved_position must contain three coordinates")
    matrix = _matrix3(point["rotation_quaternion_xyzw"], assignment["target_scale_xyz"])
    output.write(
        (
            "    <ModelPoint>\n"
            f"        <ModelName>{escape(model_name)}</ModelName>\n"
            "        <LocationX>"
            f"{_number(position[0] + offset[0])}</LocationX>\n"
            "        <LocationY>"
            f"{_number(position[1] + offset[1])}</LocationY>\n"
            "        <LocationZ>"
            f"{_number(position[2] + offset[2])}</LocationZ>\n"
            "        <Matrix3>"
            + ",".join(_number(value) for value in matrix)
            + "</Matrix3>\n"
            "    </ModelPoint>\n"
        ).encode("utf-8")
    )


def _model_names(binding: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for asset in binding["asset_use"]["assets"]:
        asset_id = asset.get("asset_id")
        metadata = asset.get("provider_metadata")
        model_name = metadata.get("model_name") if isinstance(metadata, dict) else None
        if not isinstance(asset_id, str) or not asset_id.strip():
            raise ValueError("DTS catalog asset_id is required")
        if not isinstance(model_name, str) or not model_name.strip():
            raise ValueError(f"DTS model_name is required for asset: {asset_id}")
        if asset_id in result:
            raise ValueError(f"Duplicate DTS catalog asset: {asset_id}")
        result[asset_id] = model_name.strip()
    return result


def _matrix3(quaternion: list[float], scale: list[float]) -> tuple[float, ...]:
    x, y, z, w = _finite_vector(tuple(quaternion), "quaternion")
    sx, sy, sz = _finite_vector(tuple(scale), "scale")
    if min(sx, sy, sz) <= 0:
        raise ValueError("DTS scale must be positive")
    norm = math.sqrt(x * x + y * y + z * z + w * w)
    if norm <= 1e-15:
        raise ValueError("DTS rotation quaternion cannot be zero")
    x, y, z, w = x / norm, y / norm, z / norm, w / norm
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    # DTS Matrix3 writes each transformed local axis as a consecutive triple.
    return (
        (1.0 - 2.0 * (yy + zz)) * sx,
        2.0 * (xy + wz) * sx,
        2.0 * (xz - wy) * sx,
        2.0 * (xy - wz) * sy,
        (1.0 - 2.0 * (xx + zz)) * sy,
        2.0 * (yz + wx) * sy,
        2.0 * (xz + wy) * sz,
        2.0 * (yz - wx) * sz,
        (1.0 - 2.0 * (xx + yy)) * sz,
    )


def _finite_vector(
    value: tuple[object, ...],
    field: str,
) -> tuple[float, float, float] | tuple[float, float, float, float]:
    if len(value) not in {3, 4}:
        raise ValueError(f"{field} has an invalid vector length")
    if any(not isinstance(item, (int, float)) or isinstance(item, bool) for item in value):
        raise ValueError(f"{field} values must be numeric")
    result = tuple(float(item) for item in value)
    if not all(math.isfinite(item) for item in result):
        raise ValueError(f"{field} values must be finite")
    return result


def _number(value: float) -> str:
    if abs(value) < 0.5e-6:
        value = 0.0
    return f"{value:.6f}"
