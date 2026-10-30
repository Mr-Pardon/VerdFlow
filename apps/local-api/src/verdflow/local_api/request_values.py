"""Stateless Local API request values; no Host or HTTP lifecycle."""

from __future__ import annotations

import math
from typing import Any

from verdflow.contracts import JobStatus


def bounded_text_list(document: dict, name: str, *, limit: int = 128) -> tuple[str, ...]:
    value = document.get(name, [])
    if not isinstance(value, list) or len(value) > limit or any(
        not isinstance(item, str) or not item.strip() or len(item) > 4096 for item in value
    ):
        raise ValueError(f"{name} must be a bounded list of non-empty strings")
    if len(set(value)) != len(value):
        raise ValueError(f"{name} must not contain duplicates")
    return tuple(value)


def required_text(document: dict[str, Any], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value.strip()


def required_integer(
    document: dict[str, Any],
    field: str,
    *,
    minimum: int,
) -> int:
    value = document.get(field)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    if value < minimum:
        raise ValueError(f"{field} must be at least {minimum}")
    return value


def optional_boolean(
    document: dict[str, Any],
    field: str,
    *,
    default: bool,
) -> bool:
    value = document.get(field, default)
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def optional_text(
    document: dict[str, Any],
    field: str,
) -> str | None:
    value = document.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def pop_idempotency_key(document: dict[str, Any]) -> str | None:
    value = document.pop("idempotency_key", None)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            "idempotency_key must be a non-empty string"
        )
    return value.strip()


def reject_unknown(document: dict[str, Any]) -> None:
    if document:
        raise ValueError(
            "Unexpected request fields: "
            + ", ".join(sorted(document))
        )


def query_integer(
    query: dict[str, list[str]],
    field: str,
    *,
    default: int,
    minimum: int,
    maximum: int | None = None,
) -> int:
    values = query.get(field)
    if values is None:
        return default
    if len(values) != 1:
        raise ValueError(f"{field} must be supplied once")
    try:
        value = int(values[0])
    except ValueError as error:
        raise ValueError(f"{field} must be an integer") from error
    if value < minimum or (
        maximum is not None and value > maximum
    ):
        bounds = (
            f"between {minimum} and {maximum}"
            if maximum is not None
            else f"at least {minimum}"
        )
        raise ValueError(f"{field} must be {bounds}")
    return value


def query_optional_text(
    query: dict[str, list[str]],
    field: str,
) -> str | None:
    values = query.get(field)
    if values is None:
        return None
    if len(values) != 1 or not values[0]:
        raise ValueError(f"{field} must be supplied once")
    return values[0]


def query_required_text(
    query: dict[str, list[str]],
    field: str,
) -> str:
    values = query.get(field)
    if values is None or len(values) != 1 or not values[0]:
        raise ValueError(f"{field} must be supplied once")
    return values[0]


def query_values(
    query: dict[str, list[str]],
    field: str,
) -> tuple[str, ...]:
    result: list[str] = []
    for value in query.get(field, []):
        result.extend(item for item in value.split(",") if item)
    if len(result) != len(set(result)):
        raise ValueError(f"{field} cannot contain duplicates")
    return tuple(result)


def viewport_query(
    query: dict[str, list[str]],
) -> dict[str, float]:
    return {
        field: query_float(query, field)
        for field in ("min_x", "min_y", "max_x", "max_y")
    }


def query_float(
    query: dict[str, list[str]],
    field: str,
) -> float:
    values = query.get(field)
    if values is None or len(values) != 1:
        raise ValueError(f"{field} must be supplied once")
    try:
        value = float(values[0])
    except ValueError as error:
        raise ValueError(f"{field} must be a number") from error
    if not math.isfinite(value):
        raise ValueError(f"{field} must be finite")
    return value


def query_nonnegative_float(
    query: dict[str, list[str]],
    field: str,
    *,
    default: float,
) -> float:
    if field not in query:
        return default
    value = query_float(query, field)
    if value < 0.0:
        raise ValueError(f"{field} must be non-negative")
    return value


def query_statuses(
    query: dict[str, list[str]],
) -> tuple[JobStatus, ...]:
    values = query.get("status", [])
    statuses: list[JobStatus] = []
    for value in values:
        for item in value.split(","):
            if item:
                statuses.append(JobStatus(item))
    if len(statuses) != len(set(statuses)):
        raise ValueError("status cannot contain duplicates")
    return tuple(statuses)
