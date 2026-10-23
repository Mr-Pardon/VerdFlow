"""Optional Profile values, separate from strategy-owned parameter definitions."""

from __future__ import annotations

import copy
import math
from typing import Any

from .errors import PlantingDocumentError


def validate_parameter_values(values: object) -> dict[str, float]:
    if not isinstance(values, dict):
        raise PlantingDocumentError("strategy_parameters must be an object")
    for key, value in values.items():
        if (
            not isinstance(key, str)
            or isinstance(value, bool)
            or not isinstance(value, (int, float))
            or abs(value) > 1.7976931348623157e308
            or not math.isfinite(value)
        ):
            raise PlantingDocumentError("strategy_parameters requires finite numeric values")
    return dict(values)


def validate_profile_parameters(strategy: dict[str, Any], profile: dict[str, Any]) -> None:
    if "strategy_parameters" not in profile:
        return
    values = validate_parameter_values(profile["strategy_parameters"])
    declarations = {item["key"]: item for item in strategy.get("generation_parameters", [])}
    for key, value in values.items():
        parameter = declarations.get(key)
        if parameter is None:
            raise PlantingDocumentError(f"Unknown public strategy parameter {key!r}")
        if not parameter["min"] <= value <= parameter["max"]:
            raise PlantingDocumentError(f"Strategy parameter {key!r} is outside its declared range")


def profile_generation_args(
    profile: dict[str, Any] | None,
    generation_args: dict[str, Any],
) -> dict[str, Any]:
    """Freeze preset values below explicit run overrides, without mutating either."""
    result = copy.deepcopy(generation_args)
    if profile is None or "strategy_parameters" not in profile:
        return result
    preset = validate_parameter_values(profile["strategy_parameters"])
    overrides = validate_parameter_values(result.get("strategy_parameters", {}))
    result["strategy_parameters"] = {**preset, **overrides}
    return result
