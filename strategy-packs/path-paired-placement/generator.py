"""Open-path station placement; no assets, scales, sampling or target protocols."""

import bisect
import hashlib
import math

MAX_INSTANCES = 100000


def _mix(a, b, fraction):
    return [x + (y - x) * fraction for x, y in zip(a, b)]


def _segments(coordinates, radius):
    lengths = [math.hypot(b[0] - a[0], b[1] - a[1])
               for a, b in zip(coordinates, coordinates[1:])]
    if not lengths or any(not math.isfinite(length) or length <= 0 for length in lengths):
        raise ValueError("路径包含无效或零长度线段")
    headings = [math.atan2(b[1] - a[1], b[0] - a[0])
                for a, b in zip(coordinates, coordinates[1:])]
    start = coordinates[0]
    segments = []
    for i, end in enumerate(coordinates[1:]):
        arc = None
        exit_point = end
        heading = headings[i]
        if radius and i + 1 < len(lengths):
            turn = math.remainder(headings[i + 1] - heading, math.tau)
            if 1e-8 < abs(turn) < math.pi - 1e-8:
                # Each corner can use at most half of either adjoining segment.
                factor = math.tan(abs(turn) / 2)
                trim = min(radius * factor, lengths[i] / 2, lengths[i + 1] / 2)
                if trim > 1e-9:
                    actual_radius = trim / factor
                    entry = _mix(end, coordinates[i], trim / lengths[i])
                    exit_point = _mix(end, coordinates[i + 2], trim / lengths[i + 1])
                    sign = math.copysign(1, turn)
                    center = (entry[0] - sign * actual_radius * math.sin(heading),
                              entry[1] + sign * actual_radius * math.cos(heading))
                    arc = (entry, exit_point, actual_radius * abs(turn), heading,
                           (center, actual_radius, turn))
                    end = entry
        length = math.hypot(end[0] - start[0], end[1] - start[1])
        if length > 1e-9:
            segments.append((start, end, length, heading, None))
        if arc is not None:
            segments.append(arc)
        start = exit_point
    return segments


def _parameters(context):
    return {
        **context["strategy"].get("parameters", {}),
        **context.get("args", {}).get("strategy_parameters", {}),
    }


def _evaluate(segment, fraction):
    a, b, _, tangent, arc = segment
    position = _mix(a, b, fraction)
    if arc is not None:
        center, radius, turn = arc
        tangent += turn * fraction
        radial = tangent - math.copysign(math.pi / 2, turn)
        position[:2] = [
            center[0] + radius * math.cos(radial),
            center[1] + radius * math.sin(radial),
        ]
    return position, tangent


def preview_path_xy(context):
    """Sample the same segments used to place instances, for display only."""
    radius = float(_parameters(context).get("corner_radius_m", 0))
    if not radius:
        return None
    (path,) = context["selected_paths"]
    segments = _segments(path["coordinates"], radius)
    if not any(segment[4] for segment in segments):
        return None
    outline = [segments[0][0][:2]]
    for segment in segments:
        arc = segment[4]
        steps = math.ceil(abs(arc[2]) / (math.pi / 24)) if arc else 1
        for index in range(1, steps + 1):
            position, _ = _evaluate(segment, index / steps)
            outline.append(position[:2])
    return outline


def generate(context):
    strategy = context["strategy"]
    parameters = _parameters(context)
    spacing = float(parameters.get("spacing_m", 8))
    offset = float(parameters.get("start_offset_m", 0))
    radius = float(parameters.get("corner_radius_m", 0))
    if (
        not all(math.isfinite(v) for v in (spacing, offset, radius))
        or spacing < 0.5
        or offset < 0
        or not 0 <= radius <= 50
    ):
        raise ValueError("沿线间距、起点留距或转角圆滑半径无效")
    roles = [
        role["role_key"]
        for role in strategy["roles"]
        if role["role_key"] not in context.get("excluded_role_keys", [])
    ]
    if not roles:
        raise ValueError("请至少启用一个布置角色")
    prepared = []
    total = 0
    for path in context["selected_paths"]:
        coordinates = path["coordinates"]
        if len(coordinates) < 2 or coordinates[0][:2] == coordinates[-1][:2]:
            raise ValueError("当前策略仅接受有效的开放折线")
        segments = _segments(coordinates, radius)
        cumulative = [0.0]
        for _, _, length, _, _ in segments:
            cumulative.append(cumulative[-1] + length)
        length_xy = cumulative[-1]
        if not math.isfinite(length_xy) or length_xy <= 0:
            raise ValueError("路径长度无效")
        count = max(0, math.floor((length_xy - offset) / spacing + 1e-10) + 1)
        total += count * len(roles)
        if total > MAX_INSTANCES:
            raise ValueError(
                f"预计生成 {total:,} 个实例，超过 {MAX_INSTANCES:,} 个上限，请增大间距或减少路径"
            )
        prepared.append((path, count, segments, cumulative))
    if not total:
        raise ValueError("路径短于起点留距，没有可布置的位置，请减小起点留距")
    points = []
    for path, count, segments, cumulative in prepared:
        for station in range(count):
            distance = min(cumulative[-1], offset + station * spacing)
            segment = min(
                len(segments) - 1,
                bisect.bisect_right(cumulative, distance + 1e-10) - 1,
            )
            length = segments[segment][2]
            fraction = min(1, max(0, (distance - cumulative[segment]) / length))
            position, tangent = _evaluate(segments[segment], fraction)
            group = f"{path['id']}:station:{station}"
            for role in roles:
                identifier = f"{group}:{role}"
                # Tangents are measured from +X; tree pits face local +Y.
                yaw = tangent - math.pi / 2
                if role == "vegetation":
                    digest = hashlib.sha256(
                        f"{context['seed']}:{identifier}:yaw".encode()
                    ).digest()
                    unit = (int.from_bytes(digest[:8], "big") >> 11) / 2**53
                    yaw = math.tau * unit
                points.append(
                    {
                        "id": identifier,
                        "path_id": path["id"],
                        "role_key": role,
                        "group_id": group,
                        "generated_position": position.copy(),
                        "terrain_sample": None,
                        "resolved_position": position.copy(),
                        "rotation_quaternion_xyzw": [
                            0.0,
                            0.0,
                            math.sin(yaw / 2),
                            math.cos(yaw / 2),
                        ],
                    }
                )
    return {
        "schema": "verdflow.points",
        "version": "3",
        "strategy": {
            "strategy_id": strategy["strategy_id"],
            "strategy_version": strategy["strategy_version"],
        },
        "coordinate_space": "source_scene_world",
        "offset_applied": False,
        "kind": "generated",
        "resolution_policy": "generated_position",
        "point_count": len(points),
        "points": points,
    }
