"""Durable, linear Plant Run coordination without a general workflow engine."""

from __future__ import annotations

import json
from contextlib import AbstractContextManager
from dataclasses import dataclass, field, replace
from typing import Any, BinaryIO, Protocol

from verdflow.contracts import (
    JobInfo,
    JobStatus,
    PLANT_RUN_CONTINUATION_SCHEMA,
    PLANT_RUN_CONTINUATION_VERSION,
    PLANT_RUN_EXECUTION_REQUEST_SCHEMA,
    PLANT_RUN_EXECUTION_REQUEST_VERSION,
    PlantRunExecutionInfo,
    PlantRunReviewPolicy,
    PlantRunExecutionStage,
    PlantRunExecutionStatus,
)
from verdflow.planting import build_binding_draft, canonical_json_bytes, semantic_hash, normalize_asset_selection
from verdflow.project_store import ProjectStore

from .asset_assignment import AssetAssignmentRequest
from .elevation_sampling import ElevationSamplingRequest
from .exporting import OutputExportRequest
from .job_runtime import JobRuntime
from .planting_generation import PlantingGenerationRequest


@dataclass(frozen=True, slots=True)
class PlantRunExecutionRequest:
    scene_revision_id: str
    region_set_id: str
    selected_region_ids: tuple[str, ...]
    strategy_package_hash: str
    random_seed: int
    target_type: str
    catalog_id: str | None = None
    review_policy: PlantRunReviewPolicy = PlantRunReviewPolicy.AUTOMATIC
    elevation_provider_id: str | None = None
    export_provider_id: str | None = None
    offset: tuple[float, float, float] | None = None
    profile_id: str | None = None
    assignment_random_seed: int | None = None
    generation_args: dict[str, Any] = field(default_factory=dict)
    elevation_options: dict[str, Any] = field(default_factory=dict)
    binding_overrides: dict[str, Any] = field(default_factory=dict)
    profile_snapshot: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class PlantRunContinuationRequest:
    elevation_provider_id: str
    export_provider_id: str
    offset: tuple[float, float, float]
    elevation_options: dict[str, Any] = field(default_factory=dict)
    selected_asset_ids: tuple[str, ...] | None = None
    complete_after_elevation: bool = False


class PlantRunExecutionReader(Protocol):
    def get_plant_run_execution(self, execution_id: str) -> PlantRunExecutionInfo: ...

    def open_artifact(self, artifact_id: str) -> AbstractContextManager[BinaryIO]: ...


def load_plant_run_execution_request(
    reader: PlantRunExecutionReader,
    execution_id: str,
) -> PlantRunExecutionRequest:
    """Restore immutable execution input without an execution-thread dependency."""
    return _load_execution_request(reader, reader.get_plant_run_execution(execution_id))


def _load_execution_request(
    reader: PlantRunExecutionReader,
    info: PlantRunExecutionInfo,
) -> PlantRunExecutionRequest:
    with reader.open_artifact(info.request_artifact_id) as stream:
        document = json.load(stream)
    if not isinstance(document, dict):
        raise ValueError("Plant Run request must be a JSON object")
    if semantic_hash(document) != info.request_hash:
        raise ValueError("Plant Run request hash differs from execution")
    return PlantRunExecutionService._request_from_document(document)


class PlantRunExecutionStages(Protocol):
    def freeze_profile(self, profile_id: str | None) -> dict[str, Any] | None: ...

    def freeze_generation_args(self, profile_id: str | None, args: dict[str, Any]) -> dict[str, Any]: ...

    def freeze_role_assignments(self, profile_id: str | None, overrides: dict[str, Any]) -> dict[str, Any]: ...

    def submit_planting_generation(
        self,
        request: PlantingGenerationRequest,
        *,
        idempotency_key: str | None = None,
    ) -> JobInfo: ...

    def finalize_generated_points(self, job_id: str): ...

    def submit_asset_assignment(
        self,
        request: AssetAssignmentRequest,
        *,
        idempotency_key: str | None = None,
    ) -> JobInfo: ...

    def finalize_asset_assignment(self, job_id: str): ...

    def submit_elevation_sampling(
        self,
        request: ElevationSamplingRequest,
        *,
        idempotency_key: str | None = None,
    ) -> JobInfo: ...

    def finalize_elevation(self, job_id: str): ...

    def submit_output_export(
        self,
        request: OutputExportRequest,
        *,
        idempotency_key: str | None = None,
    ) -> JobInfo: ...

    def finalize_output_export(self, job_id: str): ...


class PlantRunExecutionService:
    """Persist and advance at most one stage transition per service cycle."""

    _TERMINAL = {
        PlantRunExecutionStatus.CANCELLED,
        PlantRunExecutionStatus.FAILED,
        PlantRunExecutionStatus.SUCCEEDED,
    }
    _JOB_TERMINAL = {
        JobStatus.CANCELLED,
        JobStatus.FAILED,
        JobStatus.INTERRUPTED,
        JobStatus.SUCCEEDED,
    }

    def __init__(
        self,
        store: ProjectStore,
        runtime: JobRuntime,
        stages: PlantRunExecutionStages,
    ) -> None:
        self._store = store
        self._runtime = runtime
        self._stages = stages

    def submit(
        self,
        request: PlantRunExecutionRequest,
        *,
        idempotency_key: str | None = None,
    ) -> PlantRunExecutionInfo:
        self._validate_request(request)
        request = replace(
            request,
            profile_snapshot=self._stages.freeze_profile(request.profile_id),
            binding_overrides=self._stages.freeze_role_assignments(request.profile_id, request.binding_overrides),
            generation_args=self._stages.freeze_generation_args(request.profile_id, request.generation_args),
        )
        document = self._document(request)
        request_hash = semantic_hash(document)
        artifact = self._store.put_bytes(
            canonical_json_bytes(document),
            media_type="application/vnd.verdflow.request+json",
            schema_id=PLANT_RUN_EXECUTION_REQUEST_SCHEMA,
            schema_version=PLANT_RUN_EXECUTION_REQUEST_VERSION,
        )
        return self._store.create_plant_run_execution(
            idempotency_key=(
                idempotency_key
                or "plant-run-execution:"
                + request_hash.removeprefix("sha256:")
            ),
            request_artifact_id=artifact.artifact_id,
            request_hash=request_hash,
        )

    def get(self, execution_id: str) -> PlantRunExecutionInfo:
        return self._store.get_plant_run_execution(execution_id)

    def get_request(self, execution_id: str) -> PlantRunExecutionRequest:
        return self._load_request(self.get(execution_id))

    def confirm(
        self,
        execution_id: str,
        continuation: PlantRunContinuationRequest,
    ) -> PlantRunExecutionInfo:
        document = self._continuation_document(continuation)
        continuation_hash = semantic_hash(document)
        info = self.get(execution_id)
        if info.continuation_hash is not None:
            if info.continuation_hash != continuation_hash:
                raise RuntimeError(
                    "Plant Run was already confirmed with different output parameters"
                )
            return info
        if (
            info.status is not PlantRunExecutionStatus.AWAITING_CONFIRMATION
            or info.stage is not PlantRunExecutionStage.REVIEW
        ):
            raise RuntimeError("Plant Run is not awaiting Review confirmation")
        self._validate_continuation(continuation)
        selected_assets = normalize_asset_selection(continuation.selected_asset_ids)
        if selected_assets is not None:
            available = {item.asset_id for item in self._store.list_asset_assignment_summaries(
                info.asset_assignment_set_id,
            )}
            unknown = set(selected_assets) - available
            if unknown:
                raise ValueError("Output references unknown assets: " + ", ".join(sorted(unknown)))
        artifact = self._store.put_bytes(
            canonical_json_bytes(document),
            media_type="application/vnd.verdflow.confirmation+json",
            schema_id=PLANT_RUN_CONTINUATION_SCHEMA,
            schema_version=PLANT_RUN_CONTINUATION_VERSION,
        )
        return self._save(
            replace(
                info,
                status=PlantRunExecutionStatus.QUEUED,
                stage=PlantRunExecutionStage.ELEVATION,
                continuation_artifact_id=artifact.artifact_id,
                continuation_hash=continuation_hash,
            )
        )

    def request_cancel(self, execution_id: str) -> PlantRunExecutionInfo:
        info = self.get(execution_id)
        if info.status in self._TERMINAL:
            return info
        requested = self._save(
            replace(info, status=PlantRunExecutionStatus.CANCEL_REQUESTED)
        )
        job_id = self._stage_job_id(requested)
        if job_id is None:
            return self._save(
                replace(
                    requested,
                    status=PlantRunExecutionStatus.CANCELLED,
                )
            )
        job = self._runtime.get(job_id)
        if job.status not in self._JOB_TERMINAL:
            self._runtime.request_cancel(job_id)
        return requested

    def retry_assignment(self, execution_id: str) -> PlantRunExecutionInfo:
        """Retry only assignment while preserving the generated PointSet."""

        info = self.get(execution_id)
        if (
            info.stage is PlantRunExecutionStage.ASSIGNMENT
            and info.status in {
                PlantRunExecutionStatus.QUEUED,
                PlantRunExecutionStatus.RUNNING,
            }
        ):
            return info
        if (
            info.status is not PlantRunExecutionStatus.FAILED
            or info.stage is not PlantRunExecutionStage.ASSIGNMENT
            or info.generated_point_set_id is None
            or info.assignment_job_id is None
        ):
            raise RuntimeError("Plant Run assignment is not retryable")
        previous_job = self._runtime.get(info.assignment_job_id)
        if previous_job.status not in {JobStatus.FAILED, JobStatus.INTERRUPTED}:
            raise RuntimeError("Plant Run assignment Job is not retryable")
        request = self._load_request(info)
        job = self._submit_assignment(
            info,
            request,
            idempotency_key=(
                f"plant-run:{execution_id}:assignment-retry:{info.revision}"
            ),
        )
        return self._save(
            replace(
                info,
                status=PlantRunExecutionStatus.QUEUED,
                assignment_job_id=job.job_id,
                error_code=None,
                error_message=None,
            )
        )

    def run_once(self) -> bool:
        active = self._store.list_active_plant_run_executions(limit=1)
        if not active:
            return False
        info = active[0]
        try:
            if info.status is PlantRunExecutionStatus.CANCEL_REQUESTED:
                return self._advance_cancel(info)
            request = self._load_request(info)
            return self._advance(info, request)
        except Exception as error:
            self._save(
                replace(
                    info,
                    status=PlantRunExecutionStatus.FAILED,
                    error_code=type(error).__name__,
                    error_message=str(error),
                )
            )
            return True

    def _advance(
        self,
        info: PlantRunExecutionInfo,
        request: PlantRunExecutionRequest,
    ) -> bool:
        if info.stage is PlantRunExecutionStage.GENERATION:
            return self._advance_generation(info, request)
        if info.stage is PlantRunExecutionStage.ASSIGNMENT:
            return self._advance_assignment(info, request)
        if info.stage is PlantRunExecutionStage.REVIEW:
            return False
        if info.stage is PlantRunExecutionStage.ELEVATION:
            return self._advance_elevation(info, request)
        if info.stage is PlantRunExecutionStage.EXPORT:
            return self._advance_export(info, request)
        raise RuntimeError("Active Plant Run execution has completed stage")

    def _advance_generation(
        self,
        info: PlantRunExecutionInfo,
        request: PlantRunExecutionRequest,
    ) -> bool:
        if info.generation_job_id is None:
            job = self._stages.submit_planting_generation(
                PlantingGenerationRequest(
                    scene_revision_id=request.scene_revision_id,
                    region_set_id=request.region_set_id,
                    selected_region_ids=request.selected_region_ids,
                    strategy_package_hash=request.strategy_package_hash,
                    random_seed=request.random_seed,
                    args=request.generation_args,
                    excluded_role_keys=tuple(sorted(
                        key for key, value in request.binding_overrides.items()
                        if value.get("enabled", True) is False
                    )),
                )
            )
            self._save(
                replace(
                    info,
                    status=PlantRunExecutionStatus.RUNNING,
                    generation_job_id=job.job_id,
                )
            )
            return True
        job = self._runtime.get(info.generation_job_id)
        if job.status not in self._JOB_TERMINAL:
            return False
        if job.status is not JobStatus.SUCCEEDED:
            self._finish_from_job(info, job)
            return True
        points = self._stages.finalize_generated_points(job.job_id)
        self._save(
            replace(
                info,
                stage=PlantRunExecutionStage.ASSIGNMENT,
                generated_point_set_id=points.point_set_id,
            )
        )
        return True

    def _advance_assignment(
        self,
        info: PlantRunExecutionInfo,
        request: PlantRunExecutionRequest,
    ) -> bool:
        if info.generated_point_set_id is None:
            raise RuntimeError("Generated PointSet is missing")
        if info.assignment_job_id is None:
            job = self._submit_assignment(info, request)
            self._save(replace(info, assignment_job_id=job.job_id))
            return True
        job = self._runtime.get(info.assignment_job_id)
        if job.status not in self._JOB_TERMINAL:
            return False
        if job.status is not JobStatus.SUCCEEDED:
            self._finish_from_job(info, job)
            return True
        assignments = self._stages.finalize_asset_assignment(job.job_id)
        if request.review_policy is PlantRunReviewPolicy.REQUIRED:
            self._save(
                replace(
                    info,
                    status=PlantRunExecutionStatus.AWAITING_CONFIRMATION,
                    stage=PlantRunExecutionStage.REVIEW,
                    asset_assignment_set_id=(
                        assignments.asset_assignment_set_id
                    ),
                )
            )
        else:
            self._save(
                replace(
                    info,
                    stage=PlantRunExecutionStage.ELEVATION,
                    asset_assignment_set_id=(
                        assignments.asset_assignment_set_id
                    ),
                )
            )
        return True

    def _submit_assignment(
        self,
        info: PlantRunExecutionInfo,
        request: PlantRunExecutionRequest,
        *,
        idempotency_key: str | None = None,
    ) -> JobInfo:
        if info.generated_point_set_id is None:
            raise RuntimeError("Generated PointSet is missing")
        seed = (
            request.random_seed
            if request.assignment_random_seed is None
            else request.assignment_random_seed
        )
        return self._stages.submit_asset_assignment(
            AssetAssignmentRequest(
                point_set_id=info.generated_point_set_id,
                target_type=request.target_type,
                catalog_id=request.catalog_id,
                random_seed=seed,
                profile_id=request.profile_id,
                overrides=request.binding_overrides,
                profile_snapshot=request.profile_snapshot,
            ),
            idempotency_key=idempotency_key,
        )

    def _advance_elevation(
        self,
        info: PlantRunExecutionInfo,
        request: PlantRunExecutionRequest,
    ) -> bool:
        if info.generated_point_set_id is None:
            raise RuntimeError("Generated PointSet is missing")
        output = self._output_parameters(info, request)
        if info.elevation_job_id is None:
            job = self._stages.submit_elevation_sampling(
                ElevationSamplingRequest(
                    parent_point_set_id=info.generated_point_set_id,
                    provider_id=output.elevation_provider_id,
                    options=output.elevation_options,
                )
            )
            self._save(replace(info, elevation_job_id=job.job_id))
            return True
        job = self._runtime.get(info.elevation_job_id)
        if job.status not in self._JOB_TERMINAL:
            return False
        if job.status is not JobStatus.SUCCEEDED:
            self._finish_from_job(info, job)
            return True
        points = self._stages.finalize_elevation(job.job_id)
        if output.complete_after_elevation:
            self._save(
                replace(
                    info,
                    status=PlantRunExecutionStatus.SUCCEEDED,
                    stage=PlantRunExecutionStage.COMPLETED,
                    elevation_point_set_id=points.point_set_id,
                )
            )
            return True
        self._save(
            replace(
                info,
                stage=PlantRunExecutionStage.EXPORT,
                elevation_point_set_id=points.point_set_id,
            )
        )
        return True

    def _advance_export(
        self,
        info: PlantRunExecutionInfo,
        request: PlantRunExecutionRequest,
    ) -> bool:
        output = self._output_parameters(info, request)
        if (
            info.elevation_point_set_id is None
            or info.asset_assignment_set_id is None
        ):
            raise RuntimeError("Export inputs are incomplete")
        if info.export_job_id is None:
            job = self._stages.submit_output_export(
                OutputExportRequest(
                    point_set_id=info.elevation_point_set_id,
                    asset_assignment_set_id=info.asset_assignment_set_id,
                    provider_id=output.export_provider_id,
                    offset=output.offset,
                    selected_asset_ids=output.selected_asset_ids,
                )
            )
            self._save(replace(info, export_job_id=job.job_id))
            return True
        job = self._runtime.get(info.export_job_id)
        if job.status not in self._JOB_TERMINAL:
            return False
        if job.status is not JobStatus.SUCCEEDED:
            self._finish_from_job(info, job)
            return True
        export = self._stages.finalize_output_export(job.job_id)
        self._save(
            replace(
                info,
                status=PlantRunExecutionStatus.SUCCEEDED,
                stage=PlantRunExecutionStage.COMPLETED,
                export_id=export.export_id,
            )
        )
        return True

    def _advance_cancel(self, info: PlantRunExecutionInfo) -> bool:
        job_id = self._stage_job_id(info)
        if job_id is not None:
            job = self._runtime.get(job_id)
            if job.status not in self._JOB_TERMINAL:
                self._runtime.request_cancel(job_id)
                return False
        self._save(
            replace(info, status=PlantRunExecutionStatus.CANCELLED)
        )
        return True

    def _finish_from_job(
        self,
        info: PlantRunExecutionInfo,
        job: JobInfo,
    ) -> None:
        if job.status is JobStatus.CANCELLED:
            self._save(
                replace(info, status=PlantRunExecutionStatus.CANCELLED)
            )
            return
        self._save(
            replace(
                info,
                status=PlantRunExecutionStatus.FAILED,
                error_code=(job.error.code if job.error else "stage_failed"),
                error_message=(
                    job.error.message if job.error else job.status.value
                ),
            )
        )

    def _save(self, info: PlantRunExecutionInfo) -> PlantRunExecutionInfo:
        return self._store.update_plant_run_execution(
            info,
            expected_revision=info.revision,
        )

    def _load_request(
        self,
        info: PlantRunExecutionInfo,
    ) -> PlantRunExecutionRequest:
        return _load_execution_request(self._store, info)

    @staticmethod
    def _stage_job_id(info: PlantRunExecutionInfo) -> str | None:
        return {
            PlantRunExecutionStage.GENERATION: info.generation_job_id,
            PlantRunExecutionStage.ASSIGNMENT: info.assignment_job_id,
            PlantRunExecutionStage.REVIEW: None,
            PlantRunExecutionStage.ELEVATION: info.elevation_job_id,
            PlantRunExecutionStage.EXPORT: info.export_job_id,
            PlantRunExecutionStage.COMPLETED: None,
        }[info.stage]

    @staticmethod
    def _document(request: PlantRunExecutionRequest) -> dict[str, Any]:
        return {
            "schema": PLANT_RUN_EXECUTION_REQUEST_SCHEMA,
            "version": PLANT_RUN_EXECUTION_REQUEST_VERSION,
            "scene_revision_id": request.scene_revision_id,
            "region_set_id": request.region_set_id,
            "selected_region_ids": list(request.selected_region_ids),
            "strategy_package_hash": request.strategy_package_hash,
            "random_seed": request.random_seed,
            "review_policy": request.review_policy.value,
            "elevation_provider_id": request.elevation_provider_id,
            "target_type": request.target_type,
            "catalog_id": request.catalog_id,
            "export_provider_id": request.export_provider_id,
            "offset": list(request.offset) if request.offset is not None else None,
            "profile_id": request.profile_id,
            "assignment_random_seed": request.assignment_random_seed,
            "generation_args": request.generation_args,
            "elevation_options": request.elevation_options,
            "binding_overrides": request.binding_overrides,
            "profile_snapshot": request.profile_snapshot,
        }

    @staticmethod
    def _request_from_document(value: dict[str, Any]) -> PlantRunExecutionRequest:
        version = str(value.get("version"))
        if (
            value.get("schema") != PLANT_RUN_EXECUTION_REQUEST_SCHEMA
            or version != PLANT_RUN_EXECUTION_REQUEST_VERSION
        ):
            raise ValueError("Plant Run request schema is unsupported")
        selected = value.get("selected_region_ids")
        offset = value.get("offset")
        if not isinstance(selected, list) or not all(
            isinstance(item, str) for item in selected
        ):
            raise ValueError("selected_region_ids is invalid")
        if offset is not None and (
            not isinstance(offset, list) or len(offset) != 3
        ):
            raise ValueError("offset is invalid")
        request = PlantRunExecutionRequest(
            scene_revision_id=str(value["scene_revision_id"]),
            region_set_id=str(value["region_set_id"]),
            selected_region_ids=tuple(selected),
            strategy_package_hash=str(value["strategy_package_hash"]),
            random_seed=int(value["random_seed"]),
            target_type=str(value["target_type"]),
            catalog_id=value.get("catalog_id"),
            review_policy=PlantRunReviewPolicy(
                value.get(
                    "review_policy",
                    PlantRunReviewPolicy.AUTOMATIC.value,
                )
            ),
            elevation_provider_id=value.get("elevation_provider_id"),
            export_provider_id=value.get("export_provider_id"),
            offset=(
                tuple(float(item) for item in offset)
                if offset is not None
                else None
            ),
            profile_id=value.get("profile_id"),
            assignment_random_seed=value.get("assignment_random_seed"),
            generation_args=dict(value.get("generation_args", {})),
            elevation_options=dict(value.get("elevation_options", {})),
            binding_overrides=dict(value.get("binding_overrides", {})),
            profile_snapshot=value.get("profile_snapshot"),
        )
        PlantRunExecutionService._validate_request(request)
        return request

    @staticmethod
    def _validate_request(request: PlantRunExecutionRequest) -> None:
        if request.profile_id is not None and (
            not isinstance(request.profile_id, str)
            or not request.profile_id.strip()
        ):
            raise ValueError("profile_id must be a non-empty string")
        if request.profile_id is None and request.catalog_id is None:
            build_binding_draft(request.binding_overrides, target_type=request.target_type)
        elif request.catalog_id is not None and (
            not isinstance(request.catalog_id, str) or not request.catalog_id.strip()
        ):
            raise ValueError("catalog_id must be a non-empty string")
        if not isinstance(request.review_policy, PlantRunReviewPolicy):
            raise ValueError("review_policy is invalid")
        if request.review_policy is PlantRunReviewPolicy.AUTOMATIC:
            PlantRunExecutionService._validate_continuation(
                PlantRunContinuationRequest(
                    elevation_provider_id=request.elevation_provider_id or "",
                    export_provider_id=request.export_provider_id or "",
                    offset=request.offset or (),
                    elevation_options=request.elevation_options,
                )
            )
        elif any(
            value is not None
            for value in (
                request.elevation_provider_id,
                request.export_provider_id,
                request.offset,
            )
        ) or request.elevation_options:
            raise ValueError(
                "Review-required Plant Run output parameters belong to confirmation"
            )

    @staticmethod
    def _validate_continuation(request: PlantRunContinuationRequest) -> None:
        normalize_asset_selection(request.selected_asset_ids)
        if not request.elevation_provider_id.strip():
            raise ValueError("elevation_provider_id is required")
        if not request.export_provider_id.strip():
            raise ValueError("export_provider_id is required")
        if len(request.offset) != 3:
            raise ValueError("offset must contain three numbers")
        if not isinstance(request.elevation_options, dict):
            raise ValueError("elevation_options must be an object")
        if not isinstance(request.complete_after_elevation, bool):
            raise ValueError("complete_after_elevation must be a boolean")

    def _output_parameters(
        self,
        info: PlantRunExecutionInfo,
        request: PlantRunExecutionRequest,
    ) -> PlantRunContinuationRequest:
        if request.review_policy is PlantRunReviewPolicy.AUTOMATIC:
            output = PlantRunContinuationRequest(
                elevation_provider_id=request.elevation_provider_id or "",
                export_provider_id=request.export_provider_id or "",
                offset=request.offset or (),
                elevation_options=request.elevation_options,
            )
            self._validate_continuation(output)
            return output
        if info.continuation_artifact_id is None or info.continuation_hash is None:
            raise RuntimeError("Plant Run Review has not been confirmed")
        with self._store.open_artifact(info.continuation_artifact_id) as stream:
            document = json.load(stream)
        if semantic_hash(document) != info.continuation_hash:
            raise ValueError("Plant Run continuation hash differs from execution")
        complete_after_elevation = document.get(
            "complete_after_elevation", False
        )
        if not isinstance(complete_after_elevation, bool):
            raise ValueError("complete_after_elevation must be a boolean")
        output = PlantRunContinuationRequest(
            elevation_provider_id=str(document["elevation_provider_id"]),
            export_provider_id=str(document["export_provider_id"]),
            offset=tuple(float(item) for item in document["offset"]),
            elevation_options=dict(document.get("elevation_options", {})),
            selected_asset_ids=normalize_asset_selection(document.get("selected_asset_ids")),
            complete_after_elevation=complete_after_elevation,
        )
        self._validate_continuation(output)
        return output

    @staticmethod
    def _continuation_document(
        request: PlantRunContinuationRequest,
    ) -> dict[str, Any]:
        selected = normalize_asset_selection(request.selected_asset_ids)
        return {
            "schema": PLANT_RUN_CONTINUATION_SCHEMA,
            "version": PLANT_RUN_CONTINUATION_VERSION,
            "elevation_provider_id": request.elevation_provider_id,
            "export_provider_id": request.export_provider_id,
            "offset": list(request.offset),
            "elevation_options": request.elevation_options,
            "selected_asset_ids": list(selected) if selected is not None else None,
            "complete_after_elevation": request.complete_after_elevation,
        }
