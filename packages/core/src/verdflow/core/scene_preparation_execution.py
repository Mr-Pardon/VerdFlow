"""Durable Source inspection and material-flow coordination."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
from typing import Any, Protocol

from verdflow.contracts import (
    JobInfo,
    JobStatus,
    MaterialConfirmationMode,
    MaterialSelectionMode,
    MaterialSelectionRequest,
    MaterialFlowExecutionStage,
    MaterialFlowExecutionStatus,
    SCENE_PREPARATION_REQUEST_SCHEMA,
    SCENE_PREPARATION_REQUEST_VERSION,
    ScenePreparationInfo,
    ScenePreparationStage,
    ScenePreparationStatus,
)
from verdflow.planting import canonical_json_bytes, semantic_hash
from verdflow.project_store import ProjectStore

from .job_runtime import JobRuntime
from .material_flow_execution import (
    MaterialFlowExecutionRequest,
    MaterialFlowExecutionService,
)


@dataclass(frozen=True, slots=True)
class ScenePreparationRequest:
    source_id: str
    analysis_provider_id: str | None
    region_provider_id: str
    confirmation_mode: MaterialConfirmationMode
    selection_mode: MaterialSelectionMode = MaterialSelectionMode.AI_ASSISTED
    model: str | None = None
    allow_contains: bool = False
    import_settings: dict[str, Any] = field(default_factory=dict)


class ScenePreparationStages(Protocol):
    def submit_scene_inspection(
        self,
        source_id: str,
        *,
        idempotency_key: str | None = None,
    ) -> JobInfo: ...

    def finalize_scene_inspection(self, job_id: str): ...


class ScenePreparationExecutionService:
    """Advance one bounded Source-to-Region transition per cycle."""

    _TERMINAL = {
        ScenePreparationStatus.CANCELLED,
        ScenePreparationStatus.FAILED,
        ScenePreparationStatus.SUCCEEDED,
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
        stages: ScenePreparationStages,
        material_flows: MaterialFlowExecutionService,
    ) -> None:
        self._store = store
        self._runtime = runtime
        self._stages = stages
        self._material_flows = material_flows

    def submit(
        self,
        request: ScenePreparationRequest,
        *,
        idempotency_key: str | None = None,
    ) -> ScenePreparationInfo:
        self._store.get_source(request.source_id)
        document = self._document(request)
        request_hash = semantic_hash(document)
        artifact = self._store.put_bytes(
            canonical_json_bytes(document),
            media_type="application/vnd.verdflow.request+json",
            schema_id=SCENE_PREPARATION_REQUEST_SCHEMA,
            schema_version=SCENE_PREPARATION_REQUEST_VERSION,
        )
        return self._store.create_scene_preparation(
            idempotency_key=(
                idempotency_key
                or "scene-preparation:" + request_hash.removeprefix("sha256:")
            ),
            request_artifact_id=artifact.artifact_id,
            request_hash=request_hash,
            source_id=request.source_id,
        )

    def get(self, execution_id: str) -> ScenePreparationInfo:
        return self._store.get_scene_preparation(execution_id)

    def confirm(
        self,
        execution_id: str,
        selection: MaterialSelectionRequest | None = None,
    ) -> ScenePreparationInfo:
        info = self.get(execution_id)
        if info.status is not ScenePreparationStatus.AWAITING_CONFIRMATION:
            if info.material_flow_execution_id is not None:
                child = self._material_flows.get(
                    info.material_flow_execution_id
                )
                if (
                    child.rule_pack_artifact_id is not None
                    and child.stage in {
                        MaterialFlowExecutionStage.EXTRACTION,
                        MaterialFlowExecutionStage.COMPLETED,
                    }
                ):
                    return info
            raise RuntimeError("Scene Preparation is not awaiting confirmation")
        if info.material_flow_execution_id is None:
            raise RuntimeError("Scene Preparation Material Flow is missing")
        if selection is None:
            self._material_flows.confirm(info.material_flow_execution_id)
        else:
            self._material_flows.confirm(
                info.material_flow_execution_id,
                selection,
            )
        return self._save(
            replace(info, status=ScenePreparationStatus.RUNNING)
        )

    def request_cancel(self, execution_id: str) -> ScenePreparationInfo:
        info = self.get(execution_id)
        if info.status in self._TERMINAL:
            return info
        requested = self._save(
            replace(info, status=ScenePreparationStatus.CANCEL_REQUESTED)
        )
        if requested.stage is ScenePreparationStage.INSPECTION:
            if requested.inspection_job_id is None:
                return self._save(
                    replace(requested, status=ScenePreparationStatus.CANCELLED)
                )
            job = self._runtime.get(requested.inspection_job_id)
            if job.status not in self._JOB_TERMINAL:
                self._runtime.request_cancel(job.job_id)
            return requested
        if requested.material_flow_execution_id is not None:
            self._material_flows.request_cancel(
                requested.material_flow_execution_id
            )
        return requested

    def run_once(self) -> bool:
        active = self._store.list_active_scene_preparations(limit=1)
        if not active:
            return False
        info = active[0]
        try:
            if info.status is ScenePreparationStatus.CANCEL_REQUESTED:
                return self._advance_cancel(info)
            request = self._load_request(info)
            if info.stage is ScenePreparationStage.INSPECTION:
                return self._advance_inspection(info, request)
            if info.stage is ScenePreparationStage.MATERIAL_FLOW:
                return self._advance_material_flow(info)
            raise RuntimeError("Active Scene Preparation has an invalid stage")
        except Exception as error:
            self._save(
                replace(
                    info,
                    status=ScenePreparationStatus.FAILED,
                    error_code=type(error).__name__,
                    error_message=str(error),
                )
            )
            return True

    def _advance_inspection(
        self,
        info: ScenePreparationInfo,
        request: ScenePreparationRequest,
    ) -> bool:
        if info.inspection_job_id is None:
            job = self._stages.submit_scene_inspection(
                request.source_id,
                idempotency_key=f"{info.execution_id}:inspection",
            )
            self._save(
                replace(
                    info,
                    status=ScenePreparationStatus.RUNNING,
                    inspection_job_id=job.job_id,
                )
            )
            return True
        job = self._runtime.get(info.inspection_job_id)
        if job.status not in self._JOB_TERMINAL:
            return False
        if job.status is not JobStatus.SUCCEEDED:
            self._finish_from_job(info, job)
            return True
        scene_revision = self._stages.finalize_scene_inspection(job.job_id)
        material_flow = self._material_flows.submit(
            MaterialFlowExecutionRequest(
                scene_revision_id=scene_revision.scene_revision_id,
                analysis_provider_id=request.analysis_provider_id,
                region_provider_id=request.region_provider_id,
                confirmation_mode=request.confirmation_mode,
                selection_mode=request.selection_mode,
                model=request.model,
                allow_contains=request.allow_contains,
                import_settings=request.import_settings,
            ),
            idempotency_key=f"{info.execution_id}:material-flow",
        )
        self._save(
            replace(
                info,
                stage=ScenePreparationStage.MATERIAL_FLOW,
                scene_revision_id=scene_revision.scene_revision_id,
                material_flow_execution_id=material_flow.execution_id,
            )
        )
        return True

    def _advance_material_flow(self, info: ScenePreparationInfo) -> bool:
        if info.material_flow_execution_id is None:
            raise RuntimeError("Scene Preparation Material Flow is missing")
        child = self._material_flows.get(info.material_flow_execution_id)
        if child.status is MaterialFlowExecutionStatus.AWAITING_CONFIRMATION:
            self._save(
                replace(
                    info,
                    status=ScenePreparationStatus.AWAITING_CONFIRMATION,
                )
            )
            return True
        if child.status in {
            MaterialFlowExecutionStatus.QUEUED,
            MaterialFlowExecutionStatus.RUNNING,
            MaterialFlowExecutionStatus.CANCEL_REQUESTED,
        }:
            return False
        if child.status is MaterialFlowExecutionStatus.SUCCEEDED:
            if child.region_set_id is None:
                raise RuntimeError("Material Flow RegionSet is missing")
            self._save(
                replace(
                    info,
                    status=ScenePreparationStatus.SUCCEEDED,
                    stage=ScenePreparationStage.COMPLETED,
                    region_set_id=child.region_set_id,
                )
            )
            return True
        if child.status is MaterialFlowExecutionStatus.CANCELLED:
            self._save(
                replace(info, status=ScenePreparationStatus.CANCELLED)
            )
            return True
        self._save(
            replace(
                info,
                status=ScenePreparationStatus.FAILED,
                error_code=child.error_code or "material_flow_failed",
                error_message=child.error_message or child.status.value,
            )
        )
        return True

    def _advance_cancel(self, info: ScenePreparationInfo) -> bool:
        if info.stage is ScenePreparationStage.INSPECTION:
            if info.inspection_job_id is not None:
                job = self._runtime.get(info.inspection_job_id)
                if job.status not in self._JOB_TERMINAL:
                    self._runtime.request_cancel(job.job_id)
                    return False
        elif info.material_flow_execution_id is not None:
            child = self._material_flows.get(info.material_flow_execution_id)
            if child.status not in {
                MaterialFlowExecutionStatus.CANCELLED,
                MaterialFlowExecutionStatus.FAILED,
                MaterialFlowExecutionStatus.SUCCEEDED,
            }:
                self._material_flows.request_cancel(child.execution_id)
                return False
        self._save(replace(info, status=ScenePreparationStatus.CANCELLED))
        return True

    def _finish_from_job(self, info: ScenePreparationInfo, job: JobInfo) -> None:
        if job.status is JobStatus.CANCELLED:
            self._save(
                replace(info, status=ScenePreparationStatus.CANCELLED)
            )
            return
        self._save(
            replace(
                info,
                status=ScenePreparationStatus.FAILED,
                error_code=job.error.code if job.error else "inspection_failed",
                error_message=job.error.message if job.error else job.status.value,
            )
        )

    def _save(self, info: ScenePreparationInfo) -> ScenePreparationInfo:
        return self._store.update_scene_preparation(
            info,
            expected_revision=info.revision,
        )

    def _load_request(self, info: ScenePreparationInfo) -> ScenePreparationRequest:
        with self._store.open_artifact(info.request_artifact_id) as stream:
            document = json.load(stream)
        if not isinstance(document, dict):
            raise ValueError("Scene Preparation request must be an object")
        if semantic_hash(document) != info.request_hash:
            raise ValueError("Scene Preparation request hash differs from execution")
        return self._request_from_document(document)

    @staticmethod
    def _document(request: ScenePreparationRequest) -> dict[str, Any]:
        if not isinstance(request.confirmation_mode, MaterialConfirmationMode):
            raise ValueError("confirmation_mode is invalid")
        if not isinstance(request.allow_contains, bool):
            raise ValueError("allow_contains must be a boolean")
        if not isinstance(request.import_settings, dict):
            raise ValueError("import_settings must be a JSON object")
        if not isinstance(request.selection_mode, MaterialSelectionMode):
            raise ValueError("selection_mode is invalid")
        analysis_provider_id = None
        if request.selection_mode is MaterialSelectionMode.AI_ASSISTED:
            analysis_provider_id = _required(
                request.analysis_provider_id,
                "analysis_provider_id",
            )
        elif request.analysis_provider_id is not None:
            raise ValueError("manual selection cannot specify analysis_provider_id")
        if (
            request.selection_mode is MaterialSelectionMode.MANUAL
            and request.confirmation_mode is not MaterialConfirmationMode.MANUAL
        ):
            raise ValueError("manual selection requires manual confirmation")
        return {
            "schema": SCENE_PREPARATION_REQUEST_SCHEMA,
            "version": SCENE_PREPARATION_REQUEST_VERSION,
            "source_id": _required(request.source_id, "source_id"),
            "selection_mode": request.selection_mode.value,
            "analysis_provider_id": analysis_provider_id,
            "region_provider_id": _required(
                request.region_provider_id, "region_provider_id"
            ),
            "confirmation_mode": request.confirmation_mode.value,
            "model": request.model,
            "allow_contains": request.allow_contains,
            "import_settings": request.import_settings,
        }

    @staticmethod
    def _request_from_document(value: dict[str, Any]) -> ScenePreparationRequest:
        version = str(value.get("version"))
        if (
            value.get("schema") != SCENE_PREPARATION_REQUEST_SCHEMA
            or version not in {"1", SCENE_PREPARATION_REQUEST_VERSION}
        ):
            raise ValueError("Scene Preparation request schema is unsupported")
        model = value.get("model")
        if model is not None:
            model = _required(model, "model")
        settings = value.get("import_settings", {})
        allow_contains = value.get("allow_contains", False)
        if not isinstance(settings, dict):
            raise ValueError("import_settings is invalid")
        if not isinstance(allow_contains, bool):
            raise ValueError("allow_contains is invalid")
        selection_mode = MaterialSelectionMode(
            value.get("selection_mode", MaterialSelectionMode.AI_ASSISTED.value)
        )
        analysis_provider_id = value.get("analysis_provider_id")
        if analysis_provider_id is not None:
            analysis_provider_id = _required(
                analysis_provider_id,
                "analysis_provider_id",
            )
        return ScenePreparationRequest(
            source_id=_required(value.get("source_id"), "source_id"),
            analysis_provider_id=analysis_provider_id,
            region_provider_id=_required(
                value.get("region_provider_id"), "region_provider_id"
            ),
            confirmation_mode=MaterialConfirmationMode(value["confirmation_mode"]),
            selection_mode=selection_mode,
            model=model,
            allow_contains=allow_contains,
            import_settings=dict(settings),
        )


def _required(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value.strip()
