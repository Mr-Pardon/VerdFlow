"""Transient assignment choices without geometry, dimensions or target locators."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AssignmentPreviewCandidate:
    asset_id: str
    weight: float
    scale_mode: str
    scale_min: float
    scale_max: float


@dataclass(frozen=True, slots=True)
class AssignmentPreviewRole:
    role_key: str
    candidates: tuple[AssignmentPreviewCandidate, ...]


@dataclass(frozen=True, slots=True)
class AssignmentPreviewRequest:
    random_seed: int
    points: tuple[tuple[str, str], ...]
    roles: tuple[AssignmentPreviewRole, ...]


@dataclass(frozen=True, slots=True)
class AssignmentPreviewResult:
    choices: tuple[tuple[int, float], ...]
