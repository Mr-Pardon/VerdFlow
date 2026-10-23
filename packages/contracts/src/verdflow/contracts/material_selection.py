"""Product-facing contracts for material facts and explicit classification."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .inference import MaterialClassification


class MaterialSelectionMode(StrEnum):
    AI_ASSISTED = "ai_assisted"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class MaterialTextureFact:
    image_name: str | None
    source_relative_path: str | None


@dataclass(frozen=True, slots=True)
class MaterialFactItem:
    material_name: str
    face_count: int
    object_count: int
    object_names: tuple[str, ...]
    diffuse_color: tuple[float, ...]
    textures: tuple[MaterialTextureFact, ...]


@dataclass(frozen=True, slots=True)
class MaterialFactsView:
    scene_revision_id: str
    materials: tuple[MaterialFactItem, ...]


@dataclass(frozen=True, slots=True)
class MaterialSelectionItem:
    material_name: str
    classification: MaterialClassification


@dataclass(frozen=True, slots=True)
class MaterialSelectionRequest:
    materials: tuple[MaterialSelectionItem, ...]
