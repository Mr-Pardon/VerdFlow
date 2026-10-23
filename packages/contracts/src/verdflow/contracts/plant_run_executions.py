"""Persistent read model for one composed planting execution."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


PLANT_RUN_EXECUTION_REQUEST_SCHEMA = "verdflow.plant-run-execution.request"
PLANT_RUN_EXECUTION_REQUEST_VERSION = "5"
PLANT_RUN_CONTINUATION_SCHEMA = "verdflow.plant-run-execution.continuation"
PLANT_RUN_CONTINUATION_VERSION = "1"


class PlantRunReviewPolicy(StrEnum):
    REQUIRED = "required"
    AUTOMATIC = "automatic"


class PlantRunExecutionStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    FAILED = "failed"
    SUCCEEDED = "succeeded"


class PlantRunExecutionStage(StrEnum):
    GENERATION = "generation"
    ASSIGNMENT = "assignment"
    REVIEW = "review"
    ELEVATION = "elevation"
    EXPORT = "export"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class PlantRunExecutionInfo:
    execution_id: str
    idempotency_key: str
    request_artifact_id: str
    request_hash: str
    continuation_artifact_id: str | None
    continuation_hash: str | None
    status: PlantRunExecutionStatus
    stage: PlantRunExecutionStage
    revision: int
    generation_job_id: str | None
    generated_point_set_id: str | None
    assignment_job_id: str | None
    asset_assignment_set_id: str | None
    elevation_job_id: str | None
    elevation_point_set_id: str | None
    export_job_id: str | None
    export_id: str | None
    error_code: str | None
    error_message: str | None
    created_at: str
    updated_at: str
