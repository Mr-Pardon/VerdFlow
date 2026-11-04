"""Validation for target-neutral open path input, not placement algorithms."""

import math
from typing import Any
from .errors import PlantingDocumentError


def strategy_input_kind(strategy: dict[str, Any]) -> str:
    kind = strategy.get("input_geometry", "polygon")
    if kind not in {"polygon", "polyline"}:
        raise PlantingDocumentError("策略必须声明一种有效的输入类型：区域或路径")
    return kind


def validate_path_document(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or value.get("schema") != "verdflow.paths"
        or value.get("version") != "1"
    ):
        raise PlantingDocumentError("路径输入格式不受支持")
    if (
        value.get("coordinate_space") != "source_scene_world"
        or value.get("offset_applied") is not False
    ):
        raise PlantingDocumentError("路径必须使用未偏移的场景坐标")
    paths = value.get("paths")
    if (
        not isinstance(paths, list)
        or not paths
        or type(value.get("path_count")) is not int
        or value["path_count"] != len(paths)
    ):
        raise PlantingDocumentError("路径集合为空或数量不一致")
    identifiers = set()
    vertices = 0
    for index, path in enumerate(paths):
        if (
            not isinstance(path, dict)
            or not isinstance(path.get("id"), str)
            or not path["id"].strip()
            or path["id"] in identifiers
        ):
            raise PlantingDocumentError(f"第 {index + 1} 条路径缺少唯一标识")
        identifiers.add(path["id"])
        coordinates = path.get("coordinates")
        if not isinstance(coordinates, list) or len(coordinates) < 2:
            raise PlantingDocumentError(f"第 {index + 1} 条路径至少需要两个顶点")
        vertices += len(coordinates)
        if vertices > 200000:
            raise PlantingDocumentError("路径顶点超过 200,000 个，请减少路径后重试")
        for point in coordinates:
            if (
                not isinstance(point, list)
                or len(point) != 3
                or any(not _finite_coordinate(v) for v in point)
            ):
                raise PlantingDocumentError(f"第 {index + 1} 条路径包含无效坐标")
        if coordinates[0][:2] == coordinates[-1][:2]:
            raise PlantingDocumentError(
                f"第 {index + 1} 条路径已闭合，当前仅支持开放折线"
            )
        if any(a[:2] == b[:2] for a, b in zip(coordinates, coordinates[1:])):
            raise PlantingDocumentError(
                f"第 {index + 1} 条路径包含零长度线段，请重新绘制"
            )
        length = sum(
            math.hypot(b[0] - a[0], b[1] - a[1])
            for a, b in zip(coordinates, coordinates[1:])
        )
        if not math.isfinite(length) or length <= 0:
            raise PlantingDocumentError(f"第 {index + 1} 条路径长度无效")
    return value


def _finite_coordinate(value: Any) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False
