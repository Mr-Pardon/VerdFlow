"""Immutable output export records shared across VerdFlow packages."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .jobs import JobStatus


COMPOSITION_OUTPUT_EXECUTION_REQUEST_SCHEMA = (
    "verdflow.composition-output-execution.request"
)
COMPOSITION_OUTPUT_EXECUTION_REQUEST_VERSION = "1"


class CompositionOutputExecutionStage(StrEnum):
    ELEVATION = "elevation"
    EXPORT = "export"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class ExportRecordInfo:
    export_id: str
    producer_job_id: str
    adapter_id: str
    adapter_version: str
    target_type: str
    point_set_id: str
    asset_assignment_set_id: str
    output_artifact_id: str
    offset_x: float
    offset_y: float
    offset_z: float
    record_count: int
    created_at: str


@dataclass(frozen=True, slots=True)
class CompositionExportInputInfo:
    ordinal: int
    batch_id: str
    point_set_id: str
    asset_assignment_set_id: str
    selected_asset_ids: tuple[str, ...] | None


@dataclass(frozen=True, slots=True)
class CompositionExportRecordInfo:
    export_id: str
    composition_id: str
    composition_generation: int
    producer_job_id: str
    adapter_id: str
    adapter_version: str
    target_type: str
    output_artifact_id: str
    offset_x: float
    offset_y: float
    offset_z: float
    record_count: int
    inputs: tuple[CompositionExportInputInfo, ...]
    created_at: str


@dataclass(frozen=True, slots=True)
class CompositionOutputExecutionInfo:
    execution_id: str
    idempotency_key: str
    request_artifact_id: str
    request_hash: str
    composition_id: str
    composition_generation: int
    status: JobStatus
    stage: CompositionOutputExecutionStage
    revision: int
    export_job_id: str | None
    export_id: str | None
    error_code: str | None
    error_message: str | None
    created_at: str
    updated_at: str
