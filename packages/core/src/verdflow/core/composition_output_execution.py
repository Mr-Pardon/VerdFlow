"""Durable deferred elevation and composition export coordination."""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from verdflow.contracts import (
    COMPOSITION_OUTPUT_EXECUTION_REQUEST_SCHEMA,
    COMPOSITION_OUTPUT_EXECUTION_REQUEST_VERSION,
    CompositionOutputExecutionInfo,
    CompositionOutputExecutionStage,
    JobInfo,
    JobStatus,
    PlantRunExecutionStatus,
)
from verdflow.planting import (
    canonical_json_bytes,
    normalize_asset_selection,
    semantic_hash,
)
from verdflow.project_store import ProjectStore

from .exporting import (
    CompositionOutputExportInput,
    CompositionOutputExportRequest,
)
from .plant_run_execution import (
    PlantRunContinuationRequest,
    PlantRunExecutionService,
)


@dataclass(frozen=True, slots=True)
class CompositionOutputBatchRequest:
    batch_id: str
    selected_asset_ids: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class CompositionOutputExecutionRequest:
    composition_id: str
    composition_generation: int
    elevation_provider_id: str
    export_provider_id: str
    offset: tuple[float, float, float]
    batches: tuple[CompositionOutputBatchRequest, ...]
    elevation_options: dict[str, Any] = field(default_factory=dict)


class CompositionOutputStages(Protocol):
    def submit_composition_output_export(
        self,
        request: CompositionOutputExportRequest,
        *,
        idempotency_key: str | None = None,
    ) -> JobInfo: ...

    def finalize_output_export(self, job_id: str): ...


class CompositionOutputExecutionService:
    """Advance one bounded composition output transition per service cycle."""

    _JOB_TERMINAL = {
        JobStatus.CANCELLED,
        JobStatus.FAILED,
        JobStatus.INTERRUPTED,
        JobStatus.SUCCEEDED,
    }

    def __init__(
        self,
        store: ProjectStore,
        runtime: Any,
        plant_runs: PlantRunExecutionService,
        stages: CompositionOutputStages,
    ) -> None:
        self._store = store
        self._runtime = runtime
        self._plant_runs = plant_runs
        self._stages = stages

    def submit(
        self,
        request: CompositionOutputExecutionRequest,
        *,
        idempotency_key: str | None = None,
    ) -> CompositionOutputExecutionInfo:
        self._validate_request(request)
        self._validate_current_composition(request)
        if self._store.list_active_composition_output_executions(limit=1):
            raise RuntimeError("A composition output execution is already active")
        document = self._document(request)
        request_hash = semantic_hash(document)
        artifact = self._store.put_bytes(
            canonical_json_bytes(document),
            media_type="application/vnd.verdflow.request+json",
            schema_id=COMPOSITION_OUTPUT_EXECUTION_REQUEST_SCHEMA,
            schema_version=COMPOSITION_OUTPUT_EXECUTION_REQUEST_VERSION,
        )
        return self._store.create_composition_output_execution(
            idempotency_key=(
                idempotency_key
                or f"composition-output:{uuid.uuid4().hex}"
            ),
            request_artifact_id=artifact.artifact_id,
            request_hash=request_hash,
            composition_id=request.composition_id,
            composition_generation=request.composition_generation,
        )

    def get(self, execution_id: str) -> CompositionOutputExecutionInfo:
        return self._store.get_composition_output_execution(execution_id)

    def get_active(self) -> CompositionOutputExecutionInfo | None:
        active = self._store.list_active_composition_output_executions(limit=1)
        return active[0] if active else None

    def run_once(self) -> bool:
        active = self._store.list_active_composition_output_executions(limit=1)
        if not active:
            return False
        info = active[0]
        try:
            request = self._load_request(info)
            if info.stage is CompositionOutputExecutionStage.ELEVATION:
                return self._advance_elevation(info, request)
            if info.stage is CompositionOutputExecutionStage.EXPORT:
                return self._advance_export(info)
            raise RuntimeError("Active composition output has completed stage")
        except Exception as error:
            self._save(
                replace(
                    info,
                    status=JobStatus.FAILED,
                    stage=CompositionOutputExecutionStage.COMPLETED,
                    error_code=type(error).__name__,
                    error_message=str(error),
                )
            )
            return True

    def _advance_elevation(
        self,
        info: CompositionOutputExecutionInfo,
        request: CompositionOutputExecutionRequest,
    ) -> bool:
        batches = self._validate_current_composition(request)
        inputs: list[CompositionOutputExportInput] = []
        for requested in request.batches:
            batch = batches[requested.batch_id]
            execution = self._plant_runs.get(batch.plant_run_execution_id)
            if execution.elevation_point_set_id is None:
                if execution.status is PlantRunExecutionStatus.AWAITING_CONFIRMATION:
                    self._plant_runs.confirm(
                        execution.execution_id,
                        PlantRunContinuationRequest(
                            elevation_provider_id=request.elevation_provider_id,
                            export_provider_id=request.export_provider_id,
                            offset=(0.0, 0.0, 0.0),
                            elevation_options=request.elevation_options,
                            complete_after_elevation=True,
                        ),
                    )
                    self._mark_running(info)
                    return True
                if execution.status in {
                    PlantRunExecutionStatus.QUEUED,
                    PlantRunExecutionStatus.RUNNING,
                    PlantRunExecutionStatus.CANCEL_REQUESTED,
                }:
                    return self._mark_running(info)
                raise RuntimeError(
                    "Planting batch elevation did not complete: "
                    + (execution.error_message or execution.status.value)
                )
            inputs.append(
                CompositionOutputExportInput(
                    batch_id=batch.batch_id,
                    point_set_id=execution.elevation_point_set_id,
                    asset_assignment_set_id=batch.asset_assignment_set_id,
                    selected_asset_ids=requested.selected_asset_ids,
                )
            )
        job = self._stages.submit_composition_output_export(
            CompositionOutputExportRequest(
                composition_id=request.composition_id,
                composition_generation=request.composition_generation,
                provider_id=request.export_provider_id,
                offset=request.offset,
                inputs=tuple(inputs),
            ),
            idempotency_key=f"{info.execution_id}:export",
        )
        self._save(
            replace(
                info,
                status=JobStatus.RUNNING,
                stage=CompositionOutputExecutionStage.EXPORT,
                export_job_id=job.job_id,
            )
        )
        return True

    def _advance_export(self, info: CompositionOutputExecutionInfo) -> bool:
        if info.export_job_id is None:
            raise RuntimeError("Composition export Job is missing")
        job = self._runtime.get(info.export_job_id)
        if job.status not in self._JOB_TERMINAL:
            return False
        if job.status is not JobStatus.SUCCEEDED:
            message = (
                job.error.message
                if job.error is not None
                else job.status.value
            )
            self._save(
                replace(
                    info,
                    status=JobStatus.FAILED,
                    stage=CompositionOutputExecutionStage.COMPLETED,
                    error_code=(
                        job.error.code
                        if job.error is not None
                        else "export_failed"
                    ),
                    error_message=message,
                )
            )
            return True
        export = self._stages.finalize_output_export(job.job_id)
        self._save(
            replace(
                info,
                status=JobStatus.SUCCEEDED,
                stage=CompositionOutputExecutionStage.COMPLETED,
                export_id=export.export_id,
            )
        )
        return True

    def _validate_current_composition(
        self,
        request: CompositionOutputExecutionRequest,
    ) -> dict[str, Any]:
        composition = self._store.current_planting_composition()
        if (
            composition is None
            or composition.composition_id != request.composition_id
            or composition.generation != request.composition_generation
        ):
            raise RuntimeError("Planting composition changed during output")
        batches = {
            batch.batch_id: batch for batch in self._store.list_planting_batches()
        }
        missing = [
            item.batch_id
            for item in request.batches
            if item.batch_id not in batches
        ]
        if missing:
            raise RuntimeError(
                "Composition output references missing batches: "
                + ", ".join(sorted(missing))
            )
        for item in request.batches:
            committed = batches[item.batch_id].selected_asset_ids
            if (
                item.selected_asset_ids is not None
                and committed is not None
                and set(item.selected_asset_ids) - set(committed)
            ):
                raise RuntimeError(
                    "Composition output cannot re-enable assets excluded "
                    "when the batch was committed"
                )
        return batches

    def _mark_running(self, info: CompositionOutputExecutionInfo) -> bool:
        if info.status is not JobStatus.RUNNING:
            self._save(replace(info, status=JobStatus.RUNNING))
            return True
        return False

    def _save(
        self,
        info: CompositionOutputExecutionInfo,
    ) -> CompositionOutputExecutionInfo:
        return self._store.update_composition_output_execution(
            info,
            expected_revision=info.revision,
        )

    def _load_request(
        self,
        info: CompositionOutputExecutionInfo,
    ) -> CompositionOutputExecutionRequest:
        with self._store.open_artifact(info.request_artifact_id) as stream:
            document = json.load(stream)
        if (
            not isinstance(document, dict)
            or semantic_hash(document) != info.request_hash
        ):
            raise ValueError("Composition output request is invalid")
        if (
            document.get("schema") != COMPOSITION_OUTPUT_EXECUTION_REQUEST_SCHEMA
            or str(document.get("version"))
            != COMPOSITION_OUTPUT_EXECUTION_REQUEST_VERSION
        ):
            raise ValueError("Composition output request schema is unsupported")
        batches = document.get("batches")
        offset = document.get("offset")
        if not isinstance(batches, list) or not isinstance(offset, list):
            raise ValueError("Composition output request fields are invalid")
        if not all(isinstance(item, dict) for item in batches):
            raise ValueError("Composition output batches are invalid")
        request = CompositionOutputExecutionRequest(
            composition_id=str(document["composition_id"]),
            composition_generation=int(document["composition_generation"]),
            elevation_provider_id=str(document["elevation_provider_id"]),
            export_provider_id=str(document["export_provider_id"]),
            offset=tuple(float(item) for item in offset),
            batches=tuple(
                CompositionOutputBatchRequest(
                    batch_id=str(item["batch_id"]),
                    selected_asset_ids=normalize_asset_selection(
                        item.get("selected_asset_ids")
                    ),
                )
                for item in batches
            ),
            elevation_options=dict(document.get("elevation_options", {})),
        )
        self._validate_request(request)
        return request

    @staticmethod
    def _document(request: CompositionOutputExecutionRequest) -> dict[str, Any]:
        return {
            "schema": COMPOSITION_OUTPUT_EXECUTION_REQUEST_SCHEMA,
            "version": COMPOSITION_OUTPUT_EXECUTION_REQUEST_VERSION,
            "composition_id": request.composition_id,
            "composition_generation": request.composition_generation,
            "elevation_provider_id": request.elevation_provider_id,
            "export_provider_id": request.export_provider_id,
            "offset": list(request.offset),
            "elevation_options": request.elevation_options,
            "batches": [
                {
                    "batch_id": item.batch_id,
                    "selected_asset_ids": (
                        list(item.selected_asset_ids)
                        if item.selected_asset_ids is not None
                        else None
                    ),
                }
                for item in request.batches
            ],
        }

    @staticmethod
    def _validate_request(request: CompositionOutputExecutionRequest) -> None:
        if (
            not request.composition_id.strip()
            or request.composition_generation < 1
        ):
            raise ValueError("Composition output identity is invalid")
        if not request.elevation_provider_id.strip():
            raise ValueError("elevation_provider_id is required")
        if not request.export_provider_id.strip():
            raise ValueError("export_provider_id is required")
        if len(request.offset) != 3:
            raise ValueError("offset must contain three numbers")
        if not all(math.isfinite(float(item)) for item in request.offset):
            raise ValueError("offset values must be finite")
        if not request.batches:
            raise ValueError("Composition output requires at least one batch")
        batch_ids = [item.batch_id for item in request.batches]
        if any(not value.strip() for value in batch_ids) or len(
            set(batch_ids)
        ) != len(batch_ids):
            raise ValueError("Composition output batch identifiers are invalid")
        for item in request.batches:
            normalize_asset_selection(item.selected_asset_ids)
        if not isinstance(request.elevation_options, dict):
            raise ValueError("elevation_options must be an object")
