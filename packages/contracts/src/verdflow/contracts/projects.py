"""Project-level contracts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProjectInfo:
    project_id: str
    name: str
    schema_version: int
    created_at: str
    updated_at: str
    active_scene_revision_id: str | None = None


@dataclass(frozen=True, slots=True)
class ProjectWorkflowState:
    """The single product-visible workflow chain for one fixed scene."""

    project_id: str
    source_id: str | None
    scene_preparation_execution_id: str | None
    material_flow_execution_id: str | None
    region_set_id: str | None
    plant_run_execution_id: str | None
    revision: int
    updated_at: str
