"""Persistent read model for Source inspection through Region preparation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


SCENE_PREPARATION_REQUEST_SCHEMA = "verdflow.scene-preparation.request"
SCENE_PREPARATION_REQUEST_VERSION = "2"


class ScenePreparationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    FAILED = "failed"
    SUCCEEDED = "succeeded"


class ScenePreparationStage(StrEnum):
    INSPECTION = "inspection"
    MATERIAL_FLOW = "material_flow"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class ScenePreparationInfo:
    execution_id: str
    idempotency_key: str
    request_artifact_id: str
    request_hash: str
    status: ScenePreparationStatus
    stage: ScenePreparationStage
    revision: int
    source_id: str
    inspection_job_id: str | None
    scene_revision_id: str | None
    material_flow_execution_id: str | None
    region_set_id: str | None
    error_code: str | None
    error_message: str | None
    created_at: str
    updated_at: str
