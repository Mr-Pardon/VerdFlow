"""Product projections derived from immutable project execution facts."""

from __future__ import annotations

import base64
import json

from verdflow.contracts import (
    JobInfo,
    JobListCursor,
    JobListPage,
    MaterialFlowExecutionInfo,
    MaterialFlowExecutionStatus,
    PlantRunExecutionInfo,
    PlantRunExecutionStatus,
    ProjectInfo,
    ProjectWorkflowState,
    ScenePreparationInfo,
    ScenePreparationStatus,
)

from .models import (
    ProjectBusinessState,
    ProjectBusinessSummary,
    ProjectTaskInfo,
    ProjectTaskPage,
)


_BUSINESS_NAMES = {
    "verdflow.blender.inspect-scene": "检查模型",
    "verdflow.model-api.analyze-materials": "分析模型材质",
    "verdflow.blender.extract-regions": "提取绿地区域",
    "verdflow.geospatial.prepare-regions": "准备地理种植区域",
    "verdflow.geospatial.sample-elevation": "采样 GeoTIFF 高程",
    "verdflow.planting.generate-points": "生成种植点位",
    "verdflow.core.assign-assets": "分配植被资产",
    "verdflow.blender.sample-elevation": "采样地形高程",
    "verdflow.dts.export-xml": "生成 DTS XML",
}


def project_business_summary(
    project: ProjectInfo,
    *,
    workflow: ProjectWorkflowState,
    scene_revision_id: str | None,
    scene_importer_id: str | None,
    scene_preparation: ScenePreparationInfo | None,
    material_flow: MaterialFlowExecutionInfo | None,
    plant_run: PlantRunExecutionInfo | None,
) -> ProjectBusinessSummary:
    material_flow_id = (
        workflow.material_flow_execution_id
        or (
            scene_preparation.material_flow_execution_id
            if scene_preparation is not None
            else None
        )
    )
    region_set_id = (
        workflow.region_set_id
        or (material_flow.region_set_id if material_flow is not None else None)
        or (
            scene_preparation.region_set_id
            if scene_preparation is not None
            else None
        )
    )
    current: tuple[str, object] | None = None
    if plant_run is not None:
        current = ("plant_run", plant_run)
    elif (
        material_flow is not None
        and (
            scene_preparation is None
            or scene_preparation.material_flow_execution_id
            != material_flow.execution_id
        )
    ):
        current = ("material_flow", material_flow)
    elif scene_preparation is not None:
        current = ("scene_preparation", scene_preparation)
    elif material_flow is not None:
        current = ("material_flow", material_flow)
    if current is None:
        return ProjectBusinessSummary(
            project_id=project.project_id,
            state=(
                ProjectBusinessState.READY_TO_PLANT
                if region_set_id is not None
                else ProjectBusinessState.NEW
            ),
            execution_kind=None,
            execution_id=None,
            execution_status=None,
            execution_stage=None,
            updated_at=workflow.updated_at,
            source_id=workflow.source_id,
            scene_revision_id=scene_revision_id,
            scene_importer_id=scene_importer_id,
            scene_preparation_execution_id=(
                workflow.scene_preparation_execution_id
            ),
            material_flow_execution_id=material_flow_id,
            region_set_id=region_set_id,
            plant_run_execution_id=workflow.plant_run_execution_id,
        )
    kind, execution = current
    return ProjectBusinessSummary(
        project_id=project.project_id,
        state=_business_state(kind, execution),
        execution_kind=kind,
        execution_id=execution.execution_id,
        execution_status=execution.status.value,
        execution_stage=execution.stage.value,
        updated_at=max(workflow.updated_at, execution.updated_at),
        source_id=workflow.source_id,
        scene_revision_id=scene_revision_id,
        scene_importer_id=scene_importer_id,
        scene_preparation_execution_id=(
            workflow.scene_preparation_execution_id
        ),
        material_flow_execution_id=material_flow_id,
        region_set_id=region_set_id,
        plant_run_execution_id=workflow.plant_run_execution_id,
        error_code=execution.error_code,
        error_message=execution.error_message,
    )


def project_task_page(page: JobListPage) -> ProjectTaskPage:
    return ProjectTaskPage(
        items=tuple(_task_info(job) for job in page.items),
        next_cursor=(
            encode_job_cursor(page.next_cursor)
            if page.next_cursor is not None
            else None
        ),
    )


def encode_job_cursor(cursor: JobListCursor) -> str:
    document = {
        "created_at": cursor.created_at,
        "job_id": cursor.job_id,
    }
    return base64.urlsafe_b64encode(
        json.dumps(document, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")


def decode_job_cursor(value: str | None) -> JobListCursor | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("before cursor is invalid")
    encoded = value.strip()
    try:
        payload = base64.urlsafe_b64decode(
            encoded + "=" * (-len(encoded) % 4)
        )
        document = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("before cursor is invalid") from error
    if not isinstance(document, dict):
        raise ValueError("before cursor is invalid")
    created_at = document.get("created_at")
    job_id = document.get("job_id")
    if not isinstance(created_at, str) or not created_at:
        raise ValueError("before cursor is invalid")
    if not isinstance(job_id, str) or not job_id:
        raise ValueError("before cursor is invalid")
    return JobListCursor(created_at=created_at, job_id=job_id)


def _task_info(job: JobInfo) -> ProjectTaskInfo:
    return ProjectTaskInfo(
        job_id=job.job_id,
        business_name=_BUSINESS_NAMES.get(
            job.command.command_type,
            "项目任务",
        ),
        status=job.status.value,
        stage=job.stage,
        progress=job.progress,
        attempt_count=job.attempt_count,
        worker_id=job.worker_id,
        heartbeat_at=job.heartbeat_at,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        error_code=job.error.code if job.error else None,
        error_message=job.error.message if job.error else None,
    )


def _business_state(kind: str, execution: object) -> ProjectBusinessState:
    if kind == "plant_run":
        status = execution.status
        if status is PlantRunExecutionStatus.AWAITING_CONFIRMATION:
            return ProjectBusinessState.AWAITING_POINT_REVIEW
        return _terminal_or_running(status)
    if kind == "scene_preparation":
        status = execution.status
        if status is ScenePreparationStatus.AWAITING_CONFIRMATION:
            return ProjectBusinessState.AWAITING_MATERIAL_CONFIRMATION
        if status is ScenePreparationStatus.SUCCEEDED:
            return ProjectBusinessState.READY_TO_PLANT
        return _terminal_or_running(status)
    if kind == "material_flow":
        status = execution.status
        if status is MaterialFlowExecutionStatus.AWAITING_CONFIRMATION:
            return ProjectBusinessState.AWAITING_MATERIAL_CONFIRMATION
        if status is MaterialFlowExecutionStatus.SUCCEEDED:
            return ProjectBusinessState.READY_TO_PLANT
        return _terminal_or_running(status)
    raise ValueError(f"Unsupported execution kind: {kind}")


def _terminal_or_running(status: object) -> ProjectBusinessState:
    value = getattr(status, "value", status)
    if value == "succeeded":
        return ProjectBusinessState.COMPLETED
    if value == "failed":
        return ProjectBusinessState.FAILED
    if value == "cancelled":
        return ProjectBusinessState.CANCELLED
    return ProjectBusinessState.RUNNING
