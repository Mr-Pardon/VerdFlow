"""Standalone isolated display projection; does not publish a PointSet."""

from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path
import sys
import time


def main() -> int:
    command_path, output_path, gate_path = map(Path, sys.argv[1:])
    while not gate_path.is_file():
        time.sleep(0.005)
    command = json.loads(command_path.read_text(encoding="utf-8"))
    root = Path(command["package_root"])
    sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location(
        "verdflow_example_generator", root / command["entry_file"]
    )
    if spec is None or spec.loader is None:
        raise ValueError("Cannot load preview generator")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    result = getattr(module, command["entry_function"])(command["context"])
    if (
        not isinstance(result, dict)
        or result.get("schema") != "verdflow.points"
        or result.get("version") != "2"
        or result.get("kind") != "generated"
        or result.get("offset_applied") is not False
    ):
        raise ValueError(
            "Example generator must return an unoffset generated PointSet v2"
        )
    raw = result.get("points")
    if (
        not isinstance(raw, list)
        or len(raw) > command["max_points"]
        or result.get("point_count") != len(raw)
    ):
        raise ValueError("Example exceeds the preview point budget")
    roles = {role: index for index, role in enumerate(command["role_keys"])}
    excluded = set(command["context"]["excluded_role_keys"])
    points = []
    point_ids = []
    seen_ids = set()
    identities_valid = True
    for point in raw:
        point_id = point.get("id")
        role = point.get("role_key")
        xyz = point.get("generated_position")
        if (
            role not in roles
            or not isinstance(xyz, list)
            or len(xyz) != 3
            or any(
                isinstance(v, bool)
                or not isinstance(v, (int, float))
                or not math.isfinite(v)
                or abs(v) > 1e9
                for v in xyz
            )
        ):
            raise ValueError("Example contains an invalid role or position")
        if (
            not isinstance(point_id, str)
            or not point_id.strip()
            or len(point_id) > 256
            or point_id in seen_ids
        ):
            identities_valid = False
        else:
            seen_ids.add(point_id)
        if role not in excluded:
            points.append([xyz[0], xyz[1], roles[role]])
            point_ids.append(point_id)
    output_path.write_text(
        json.dumps(
            {"points": points, "point_ids": point_ids if identities_valid else []},
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
