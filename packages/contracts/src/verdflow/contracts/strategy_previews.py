"""Ephemeral example previews, deliberately distinct from project PointSets."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class StrategyPreviewSample:
    display_name: str
    boundary_xy: tuple[tuple[float, float], ...]
    fixed_seed: int = 17


@dataclass(frozen=True, slots=True)
class StrategyPreviewRequest:
    package_hash: str
    strategy_parameters: dict[str, float] = field(default_factory=dict)
    excluded_role_keys: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StrategyPreviewInput:
    package_hash: str
    sample: StrategyPreviewSample
    strategy_parameters: tuple[tuple[str, float], ...]
    role_keys: tuple[str, ...]
    excluded_role_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StrategyPreviewResult:
    package_hash: str
    role_keys: tuple[str, ...]
    points: tuple[tuple[float, float, int], ...]
    point_ids: tuple[str, ...] = ()
    schema: str = "verdflow.strategy-example-preview"
    version: str = "1"


@dataclass(frozen=True, slots=True)
class StrategyPreviewOperation:
    preview_id: str
    package_hash: str
    status: str
    result: StrategyPreviewResult | None = None
    error_message: str | None = None
