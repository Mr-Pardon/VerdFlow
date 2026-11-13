"""Bounded FeatureCollection reads for local GeoJSON sources."""

from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
from typing import Any, Callable, Iterator

from .errors import GeospatialDataError


_SMALL_GEOJSON_BYTES = 4 * 1024**2


def read_geojson_features(
    path: Path, metadata: dict[str, Any], report: Callable[[str, int, int], None],
) -> Iterator[Any]:
    size = path.stat().st_size
    if size <= _SMALL_GEOJSON_BYTES:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("GeoJSON root must be an object")
        metadata["observed_crs"] = payload.get("crs")
        features = _features_from_root(payload)
        for index, feature in enumerate(features, 1):
            yield feature
            report("preparing_regions", index, len(features))
        return

    try:
        import ijson
        from ijson.common import JSONError
    except ImportError as error:
        raise GeospatialDataError(
            "geo_vector_runtime_missing",
            "地理工作进程缺少大 GeoJSON 读取依赖。请使用随应用提供的运行环境，或为自定义 Worker 环境安装地理适配器依赖。",
        ) from error

    try:
        with path.open("rb") as stream:
            events = iter(ijson.basic_parse(stream))
            if next(events, None) != ("start_map", None):
                raise ValueError("GeoJSON root must be an object")
            root: dict[str, Any] = {}
            saw_features = False
            while (field := next(events))[0] != "end_map":
                key = field[1]
                first = next(events)
                if key == "features":
                    kind = root.get("type")
                    if "type" not in root:
                        # JSON member order is not a contract. Only reordered sources
                        # need a second, native-parser scan to determine the root type.
                        with path.open("rb") as probe:
                            kind = next(ijson.items(probe, "type"), None)
                    if kind == "FeatureCollection":
                        if saw_features or first != ("start_array", None):
                            raise ValueError("GeoJSON features must be one array")
                        saw_features = True
                        while (item := next(events))[0] != "end_array":
                            yield _value(events, item, keep=True)
                            report("preparing_regions", stream.tell(), size)
                        continue
                keep = key in {"type", "crs", "geometry", "properties", "coordinates"}
                value = _value(events, first, keep=keep)
                if keep:
                    root[key] = value
            if next(events, None) is not None:
                raise ValueError("GeoJSON contains trailing data")
            metadata["observed_crs"] = root.get("crs")
            if root.get("type") == "FeatureCollection":
                if not saw_features:
                    raise ValueError("GeoJSON features must be an array")
            elif saw_features:
                raise ValueError("GeoJSON root type changed after the feature array")
            else:
                yield from _features_from_root(root)
    except (JSONError, StopIteration, UnicodeError) as error:
        raise GeospatialDataError("geo_invalid_json", "GeoJSON 文件不完整或存在语法错误，未导入区域。") from error


def _features_from_root(root: dict[str, Any]) -> list[Any]:
    kind = root.get("type")
    if kind == "FeatureCollection":
        rows = root.get("features")
        if not isinstance(rows, list):
            raise ValueError("GeoJSON features must be an array")
        return rows
    if kind == "Feature":
        return [root]
    return [{"type": "Feature", "geometry": root, "properties": {}}]


def _value(events: Iterator[tuple[str, Any]], first: tuple[str, Any], *, keep: bool) -> Any:
    from ijson.common import ObjectBuilder

    builder = ObjectBuilder() if keep else None
    event, value = first
    depth = 0
    while True:
        if event in {"start_map", "start_array"}:
            depth += 1
        elif event in {"end_map", "end_array"}:
            depth -= 1
        if builder is not None:
            builder.event(event, float(value) if isinstance(value, Decimal) else value)
        if not depth:
            return builder.value if builder is not None else None
        event, value = next(events)
