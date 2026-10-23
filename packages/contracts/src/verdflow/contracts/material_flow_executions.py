"""Persistent read model for material analysis through Region extraction."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


MATERIAL_FLOW_EXECUTION_REQUEST_SCHEMA = "verdflow.material-flow-execution.request"
MATERIAL_FLOW_EXECUTION_REQUEST_VERSION = "3"


class MaterialFlowExecutionStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    FAILED = "failed"
    SUCCEEDED = "succeeded"


class MaterialFlowExecutionStage(StrEnum):
    ANALYSIS = "analysis"
    CONFIRMATION = "confirmation"
    EXTRACTION = "extraction"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class MaterialFlowExecutionInfo:
    execution_id: str
    idempotency_key: str
    request_artifact_id: str
    request_hash: str
    status: MaterialFlowExecutionStatus
    stage: MaterialFlowExecutionStage
    revision: int
    analysis_job_id: str | None
    rule_pack_artifact_id: str | None
    extraction_job_id: str | None
    region_set_id: str | None
    error_code: str | None
    error_message: str | None
    created_at: str
    updated_at: str
