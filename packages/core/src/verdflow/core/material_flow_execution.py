"""Durable material analysis and Region extraction coordination."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
from typing import Any, Protocol

from verdflow.contracts import (
    JobInfo,
    JobStatus,
    MATERIAL_FLOW_EXECUTION_REQUEST_SCHEMA,
    MATERIAL_FLOW_EXECUTION_REQUEST_VERSION,
    MaterialConfirmationMode,
    MaterialClassification,
    MaterialSelectionItem,
    MaterialSelectionMode,
    MaterialSelectionRequest,
    MaterialFlowExecutionInfo,
    MaterialFlowExecutionStage,
    MaterialFlowExecutionStatus,
)
from verdflow.planting import canonical_json_bytes, semantic_hash
from verdflow.project_store import ProjectStore

from .job_runtime import JobRuntime
from .material_analysis import MaterialAnalysisRequest
from .material_selection import load_material_selection
from .region_extraction import RegionExtractionRequest


@dataclass(frozen=True, slots=True)
class MaterialFlowExecutionRequest:
    scene_revision_id: str
    analysis_provider_id: str | None
    region_provider_id: str
    confirmation_mode: MaterialConfirmationMode
    selection_mode: MaterialSelectionMode = MaterialSelectionMode.AI_ASSISTED
    model: str | None = None
    allow_contains: bool = False
    import_settings: dict[str, Any] = field(default_factory=dict)
    initial_selection: MaterialSelectionRequest | None = None


class MaterialFlowExecutionStages(Protocol):
    def submit_material_analysis(
        self,
        request: MaterialAnalysisRequest,
        *,
        idempotency_key: str | None = None,
    ) -> JobInfo: ...

    def load_material_candidate(self, job_id: str): ...

    def finalize_material_candidate(
        self,
        job_id: str,
        *,
        user_confirmed: bool = False,
    ): ...

    def finalize_material_selection(
        self,
        scene_revision_id: str,
        request: MaterialSelectionRequest,
        *,
        selection_mode: MaterialSelectionMode,
        candidate_job_id: str | None = None,
    ): ...

    def submit_region_extraction(
        self,
        request: RegionExtractionRequest,
        *,
        idempotency_key: str | None = None,
    ) -> JobInfo: ...

    def finalize_region_extraction(self, job_id: str): ...


class MaterialFlowExecutionService:
    """Advance one bounded material-flow transition per service cycle."""

    _TERMINAL = {
        MaterialFlowExecutionStatus.CANCELLED,
        MaterialFlowExecutionStatus.FAILED,
        MaterialFlowExecutionStatus.SUCCEEDED,
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
        stages: MaterialFlowExecutionStages,
    ) -> None:
        self._store = store
        self._runtime = runtime
        self._stages = stages

    def submit(
        self,
        request: MaterialFlowExecutionRequest,
        *,
        idempotency_key: str | None = None,
    ) -> MaterialFlowExecutionInfo:
        document = self._document(request)
        request_hash = semantic_hash(document)
        artifact = self._store.put_bytes(
            canonical_json_bytes(document),
            media_type="application/vnd.verdflow.request+json",
            schema_id=MATERIAL_FLOW_EXECUTION_REQUEST_SCHEMA,
            schema_version=MATERIAL_FLOW_EXECUTION_REQUEST_VERSION,
        )
        return self._store.create_material_flow_execution(
            idempotency_key=(
                idempotency_key
                or "material-flow-execution:"
                + request_hash.removeprefix("sha256:")
            ),
            request_artifact_id=artifact.artifact_id,
            request_hash=request_hash,
        )

    def get(self, execution_id: str) -> MaterialFlowExecutionInfo:
        return self._store.get_material_flow_execution(execution_id)

    def get_request(self, execution_id: str) -> MaterialFlowExecutionRequest:
        return self._load_request(self.get(execution_id))

    def get_selection(
        self,
        execution_id: str,
    ) -> MaterialSelectionRequest | None:
        """Reconstruct the exact confirmed selection from its immutable rule pack."""
        info = self.get(execution_id)
        if info.rule_pack_artifact_id is None:
            return None
        request = self._load_request(info)
        return load_material_selection(
            self._store,
            request.scene_revision_id,
            info.rule_pack_artifact_id,
        )

    def confirm(
        self,
        execution_id: str,
        selection: MaterialSelectionRequest | None = None,
    ) -> MaterialFlowExecutionInfo:
        info = self.get(execution_id)
        if (
            info.stage in {
                MaterialFlowExecutionStage.EXTRACTION,
                MaterialFlowExecutionStage.COMPLETED,
            }
            and info.rule_pack_artifact_id is not None
        ):
            return info
        if (
            info.status is not MaterialFlowExecutionStatus.AWAITING_CONFIRMATION
            or info.stage is not MaterialFlowExecutionStage.CONFIRMATION
        ):
            raise RuntimeError(
                "Material Flow execution is not awaiting confirmation"
            )
        request = self._load_request(info)
        if selection is None:
            if info.analysis_job_id is None:
                raise RuntimeError("Manual material selection is required")
            confirmation = self._stages.finalize_material_candidate(
                info.analysis_job_id,
                user_confirmed=True,
            )
        else:
            confirmation = self._stages.finalize_material_selection(
                request.scene_revision_id,
                selection,
                selection_mode=request.selection_mode,
                candidate_job_id=info.analysis_job_id,
            )
        return self._save(
            replace(
                info,
                status=MaterialFlowExecutionStatus.RUNNING,
                stage=MaterialFlowExecutionStage.EXTRACTION,
                rule_pack_artifact_id=confirmation.rule_pack_artifact_id,
            )
        )

    def request_cancel(self, execution_id: str) -> MaterialFlowExecutionInfo:
        info = self.get(execution_id)
        if info.status in self._TERMINAL:
            return info
        if info.status is MaterialFlowExecutionStatus.AWAITING_CONFIRMATION:
            return self._save(
                replace(info, status=MaterialFlowExecutionStatus.CANCELLED)
            )
        requested = self._save(
            replace(info, status=MaterialFlowExecutionStatus.CANCEL_REQUESTED)
        )
        job_id = self._stage_job_id(requested)
        if job_id is None:
            return self._save(
                replace(requested, status=MaterialFlowExecutionStatus.CANCELLED)
            )
        job = self._runtime.get(job_id)
        if job.status not in self._JOB_TERMINAL:
            self._runtime.request_cancel(job_id)
        return requested

    def run_once(self) -> bool:
        active = self._store.list_active_material_flow_executions(limit=1)
        if not active:
            return False
        info = active[0]
        try:
            if info.status is MaterialFlowExecutionStatus.CANCEL_REQUESTED:
                return self._advance_cancel(info)
            request = self._load_request(info)
            if info.stage is MaterialFlowExecutionStage.ANALYSIS:
                return self._advance_analysis(info, request)
            if info.stage is MaterialFlowExecutionStage.EXTRACTION:
                return self._advance_extraction(info, request)
            raise RuntimeError("Active Material Flow has an invalid stage")
        except Exception as error:
            self._save(
                replace(
                    info,
                    status=MaterialFlowExecutionStatus.FAILED,
                    error_code=type(error).__name__,
                    error_message=str(error),
                )
            )
            return True

    def _advance_analysis(
        self,
        info: MaterialFlowExecutionInfo,
        request: MaterialFlowExecutionRequest,
    ) -> bool:
        if request.selection_mode is MaterialSelectionMode.MANUAL:
            if request.initial_selection is not None:
                confirmation = self._stages.finalize_material_selection(
                    request.scene_revision_id,
                    request.initial_selection,
                    selection_mode=request.selection_mode,
                )
                self._save(
                    replace(
                        info,
                        status=MaterialFlowExecutionStatus.RUNNING,
                        stage=MaterialFlowExecutionStage.EXTRACTION,
                        rule_pack_artifact_id=(
                            confirmation.rule_pack_artifact_id
                        ),
                    )
                )
                return True
            self._save(
                replace(
                    info,
                    status=MaterialFlowExecutionStatus.AWAITING_CONFIRMATION,
                    stage=MaterialFlowExecutionStage.CONFIRMATION,
                )
            )
            return True
        if info.analysis_job_id is None:
            if request.analysis_provider_id is None:
                raise RuntimeError("AI-assisted material selection requires a provider")
            job = self._stages.submit_material_analysis(
                MaterialAnalysisRequest(
                    scene_revision_id=request.scene_revision_id,
                    provider_id=request.analysis_provider_id,
                    model=request.model,
                    confirmation_mode=request.confirmation_mode,
                ),
                idempotency_key=f"{info.execution_id}:analysis",
            )
            self._save(
                replace(
                    info,
                    status=MaterialFlowExecutionStatus.RUNNING,
                    analysis_job_id=job.job_id,
                )
            )
            return True
        job = self._runtime.get(info.analysis_job_id)
        if job.status not in self._JOB_TERMINAL:
            return False
        if job.status is not JobStatus.SUCCEEDED:
            self._finish_from_job(info, job)
            return True
        self._stages.load_material_candidate(job.job_id)
        if request.confirmation_mode is MaterialConfirmationMode.MANUAL:
            self._save(
                replace(
                    info,
                    status=MaterialFlowExecutionStatus.AWAITING_CONFIRMATION,
                    stage=MaterialFlowExecutionStage.CONFIRMATION,
                )
            )
            return True
        confirmation = self._stages.finalize_material_candidate(
            job.job_id,
            user_confirmed=False,
        )
        self._save(
            replace(
                info,
                stage=MaterialFlowExecutionStage.EXTRACTION,
                rule_pack_artifact_id=confirmation.rule_pack_artifact_id,
            )
        )
        return True

    def _advance_extraction(
        self,
        info: MaterialFlowExecutionInfo,
        request: MaterialFlowExecutionRequest,
    ) -> bool:
        if info.rule_pack_artifact_id is None:
            raise RuntimeError("Material Flow rule pack is missing")
        if info.extraction_job_id is None:
            rule_pack = self._read_json(info.rule_pack_artifact_id, "Rule pack")
            job = self._stages.submit_region_extraction(
                RegionExtractionRequest(
                    scene_revision_id=request.scene_revision_id,
                    provider_id=request.region_provider_id,
                    rule_pack=rule_pack,
                    allow_contains=request.allow_contains,
                    import_settings=request.import_settings,
                ),
                idempotency_key=f"{info.execution_id}:extraction",
            )
            self._save(replace(info, extraction_job_id=job.job_id))
            return True
        job = self._runtime.get(info.extraction_job_id)
        if job.status not in self._JOB_TERMINAL:
            return False
        if job.status is not JobStatus.SUCCEEDED:
            self._finish_from_job(info, job)
            return True
        region_set = self._stages.finalize_region_extraction(job.job_id)
        self._save(
            replace(
                info,
                status=MaterialFlowExecutionStatus.SUCCEEDED,
                stage=MaterialFlowExecutionStage.COMPLETED,
                region_set_id=region_set.region_set_id,
            )
        )
        return True

    def _advance_cancel(self, info: MaterialFlowExecutionInfo) -> bool:
        job_id = self._stage_job_id(info)
        if job_id is not None:
            job = self._runtime.get(job_id)
            if job.status not in self._JOB_TERMINAL:
                self._runtime.request_cancel(job_id)
                return False
        self._save(replace(info, status=MaterialFlowExecutionStatus.CANCELLED))
        return True

    def _finish_from_job(
        self,
        info: MaterialFlowExecutionInfo,
        job: JobInfo,
    ) -> None:
        if job.status is JobStatus.CANCELLED:
            self._save(
                replace(info, status=MaterialFlowExecutionStatus.CANCELLED)
            )
            return
        self._save(
            replace(
                info,
                status=MaterialFlowExecutionStatus.FAILED,
                error_code=job.error.code if job.error else "stage_failed",
                error_message=(
                    job.error.message if job.error else job.status.value
                ),
            )
        )

    def _save(
        self,
        info: MaterialFlowExecutionInfo,
    ) -> MaterialFlowExecutionInfo:
        return self._store.update_material_flow_execution(
            info,
            expected_revision=info.revision,
        )

    def _load_request(
        self,
        info: MaterialFlowExecutionInfo,
    ) -> MaterialFlowExecutionRequest:
        document = self._read_json(
            info.request_artifact_id,
            "Material Flow request",
        )
        if semantic_hash(document) != info.request_hash:
            raise ValueError("Material Flow request hash differs from execution")
        return self._request_from_document(document)

    def _read_json(self, artifact_id: str, label: str) -> dict[str, Any]:
        with self._store.open_artifact(artifact_id) as stream:
            document = json.load(stream)
        if not isinstance(document, dict):
            raise ValueError(f"{label} must be a JSON object")
        return document

    @staticmethod
    def _stage_job_id(info: MaterialFlowExecutionInfo) -> str | None:
        return {
            MaterialFlowExecutionStage.ANALYSIS: info.analysis_job_id,
            MaterialFlowExecutionStage.CONFIRMATION: None,
            MaterialFlowExecutionStage.EXTRACTION: info.extraction_job_id,
            MaterialFlowExecutionStage.COMPLETED: None,
        }[info.stage]

    @staticmethod
    def _document(request: MaterialFlowExecutionRequest) -> dict[str, Any]:
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
        if (
            request.initial_selection is not None
            and request.selection_mode is not MaterialSelectionMode.MANUAL
        ):
            raise ValueError("initial_selection requires manual selection mode")
        initial_selection = (
            [
                {
                    "material_name": item.material_name,
                    "classification": item.classification.value,
                }
                for item in request.initial_selection.materials
            ]
            if request.initial_selection is not None
            else None
        )
        return {
            "schema": MATERIAL_FLOW_EXECUTION_REQUEST_SCHEMA,
            "version": MATERIAL_FLOW_EXECUTION_REQUEST_VERSION,
            "scene_revision_id": _required(request.scene_revision_id, "scene_revision_id"),
            "selection_mode": request.selection_mode.value,
            "analysis_provider_id": analysis_provider_id,
            "region_provider_id": _required(
                request.region_provider_id,
                "region_provider_id",
            ),
            "confirmation_mode": request.confirmation_mode.value,
            "model": request.model,
            "allow_contains": request.allow_contains,
            "import_settings": request.import_settings,
            "initial_selection": initial_selection,
        }

    @staticmethod
    def _request_from_document(value: dict[str, Any]) -> MaterialFlowExecutionRequest:
        version = str(value.get("version"))
        if (
            value.get("schema") != MATERIAL_FLOW_EXECUTION_REQUEST_SCHEMA
            or version
            not in {"1", "2", MATERIAL_FLOW_EXECUTION_REQUEST_VERSION}
        ):
            raise ValueError("Material Flow request schema is unsupported")
        model = value.get("model")
        if model is not None:
            model = _required(model, "model")
        allow_contains = value.get("allow_contains", False)
        import_settings = value.get("import_settings", {})
        if not isinstance(allow_contains, bool):
            raise ValueError("allow_contains is invalid")
        if not isinstance(import_settings, dict):
            raise ValueError("import_settings is invalid")
        initial_selection_value = value.get("initial_selection")
        initial_selection = None
        if initial_selection_value is not None:
            if not isinstance(initial_selection_value, list):
                raise ValueError("initial_selection is invalid")
            initial_selection = MaterialSelectionRequest(
                materials=tuple(
                    MaterialSelectionItem(
                        material_name=_required(item.get("material_name"), "material_name"),
                        classification=MaterialClassification(
                            item.get("classification")
                        ),
                    )
                    for item in initial_selection_value
                    if isinstance(item, dict)
                )
            )
            if len(initial_selection.materials) != len(initial_selection_value):
                raise ValueError("initial_selection item is invalid")
        selection_mode = MaterialSelectionMode(
            value.get("selection_mode", MaterialSelectionMode.AI_ASSISTED.value)
        )
        analysis_provider_id = value.get("analysis_provider_id")
        if analysis_provider_id is not None:
            analysis_provider_id = _required(
                analysis_provider_id,
                "analysis_provider_id",
            )
        return MaterialFlowExecutionRequest(
            scene_revision_id=_required(value.get("scene_revision_id"), "scene_revision_id"),
            analysis_provider_id=analysis_provider_id,
            region_provider_id=_required(
                value.get("region_provider_id"),
                "region_provider_id",
            ),
            confirmation_mode=MaterialConfirmationMode(value["confirmation_mode"]),
            selection_mode=selection_mode,
            model=model,
            allow_contains=allow_contains,
            import_settings=dict(import_settings),
            initial_selection=initial_selection,
        )


def _required(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value.strip()
