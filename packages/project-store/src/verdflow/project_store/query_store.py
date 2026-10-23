"""Read-only ProjectStore projection for transport and monitoring queries."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator

from verdflow.contracts import (
    ArtifactRef,
    AssetAssignmentSetInfo,
    AssetAssignmentSummary,
    CompositionExportRecordInfo,
    CompositionOutputExecutionInfo,
    EffectiveBindingSnapshotInfo,
    ExportRecordInfo,
    JobAttemptInfo,
    JobEvent,
    JobInfo,
    JobListCursor,
    JobListPage,
    JobStatus,
    MaterialFlowExecutionInfo,
    PlantRunExecutionInfo,
    PlantingBatchInfo,
    PlantingCompositionInfo,
    PlantingRunInfo,
    PointRoleSummary,
    PointSetInfo,
    ProjectInfo,
    ProjectWorkflowState,
    RegionSetInfo,
    RegionSummary,
    ScenePreparationInfo,
    SceneRevisionInfo,
    TargetAssetUseSnapshotInfo,
)

from .artifacts import ArtifactRepository
from .composition_exports import CompositionExportRepository
from .composition_output_executions import CompositionOutputExecutionRepository
from .database import ReadOnlyProjectDatabase
from .errors import ProjectNotFoundError
from .exports import ExportRepository
from .jobs import JobRepository
from .layout import ProjectLayout
from .material_flow_executions import MaterialFlowExecutionRepository
from .object_store import ContentAddressedObjectStore
from .plant_run_executions import PlantRunExecutionRepository
from .planting_compositions import PlantingCompositionRepository
from .points import PointRepository
from .projects import ProjectRepository
from .regions import RegionRepository
from .scene_preparation_executions import ScenePreparationRepository
from .scenes import SceneRepository
from .schema import validate_schema
from .snapshots import ProjectSnapshotRepository
from .workflow import ProjectWorkflowRepository


class ProjectQueryStore:
    """Expose bounded project reads without acquiring the project writer lock."""

    def __init__(self, root: Path) -> None:
        self._layout = ProjectLayout.from_root(root)
        self._database = ReadOnlyProjectDatabase(self._layout.database)
        self._artifacts = ArtifactRepository(self._database)
        self._objects = ContentAddressedObjectStore(self._layout)
        self._jobs = JobRepository(self._database)
        self._exports = ExportRepository(self._database)
        self._composition_exports = CompositionExportRepository(self._database)
        self._composition_output_executions = (
            CompositionOutputExecutionRepository(self._database)
        )
        self._projects = ProjectRepository(self._database)
        self._material_flow_executions = MaterialFlowExecutionRepository(
            self._database
        )
        self._scene_preparations = ScenePreparationRepository(self._database)
        self._scenes = SceneRepository(self._database)
        self._plant_run_executions = PlantRunExecutionRepository(self._database)
        self._planting_compositions = PlantingCompositionRepository(
            self._database
        )
        self._regions = RegionRepository(self._database)
        self._points = PointRepository(self._database)
        self._snapshots = ProjectSnapshotRepository(self._database)
        self._workflow = ProjectWorkflowRepository(self._database)

    @classmethod
    def open(cls, root: Path) -> ProjectQueryStore:
        store = cls(root)
        if not store._layout.database.is_file():
            raise ProjectNotFoundError(str(store._layout.database))
        try:
            store._database.open()
            validate_schema(store._database)
        except BaseException:
            store.close()
            raise
        return store

    def __enter__(self) -> ProjectQueryStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._database.close()

    def project_info(self) -> ProjectInfo:
        with self._database.snapshot():
            return self._projects.get_info()

    def project_workflow_state(self) -> ProjectWorkflowState:
        with self._database.snapshot():
            return self._workflow.get()

    def current_planting_composition(
        self,
    ) -> PlantingCompositionInfo | None:
        with self._database.snapshot():
            return self._planting_compositions.get_current()

    def list_planting_batches(self) -> tuple[PlantingBatchInfo, ...]:
        with self._database.snapshot():
            return self._planting_compositions.list_batches()

    def current_workflow_executions(self) -> tuple[
        ProjectWorkflowState,
        ScenePreparationInfo | None,
        MaterialFlowExecutionInfo | None,
        PlantRunExecutionInfo | None,
    ]:
        with self._database.snapshot():
            workflow = self._workflow.get()
            preparation = (
                self._scene_preparations.get(
                    workflow.scene_preparation_execution_id
                )
                if workflow.scene_preparation_execution_id is not None
                else None
            )
            material_flow_id = workflow.material_flow_execution_id
            if (
                material_flow_id is None
                and preparation is not None
                and preparation.material_flow_execution_id is not None
            ):
                material_flow_id = preparation.material_flow_execution_id
            material_flow = (
                self._material_flow_executions.get(material_flow_id)
                if material_flow_id is not None
                else None
            )
            plant_run = (
                self._plant_run_executions.get(
                    workflow.plant_run_execution_id
                )
                if workflow.plant_run_execution_id is not None
                else None
            )
            return workflow, preparation, material_flow, plant_run

    def get_job(self, job_id: str) -> JobInfo:
        with self._database.snapshot():
            return self._jobs.get(job_id)

    def list_jobs(
        self,
        *,
        statuses: tuple[JobStatus, ...] = (),
        before: JobListCursor | None = None,
        limit: int = 50,
    ) -> JobListPage:
        with self._database.snapshot():
            return self._jobs.list_page(
                statuses=statuses,
                before=before,
                limit=limit,
            )

    def list_job_events(
        self,
        job_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> tuple[JobEvent, ...]:
        with self._database.snapshot():
            return self._jobs.list_events(
                job_id,
                after_sequence=after_sequence,
                limit=limit,
            )

    def list_job_attempts(self, job_id: str) -> tuple[JobAttemptInfo, ...]:
        with self._database.snapshot():
            return self._jobs.list_attempts(job_id)

    def get_artifact(self, artifact_id: str) -> ArtifactRef:
        with self._database.snapshot():
            return self._artifacts.get(artifact_id)

    def get_export_record(self, export_id: str) -> ExportRecordInfo:
        with self._database.snapshot():
            return self._exports.get(export_id)

    def get_composition_export_record(
        self, export_id: str
    ) -> CompositionExportRecordInfo:
        with self._database.snapshot():
            return self._composition_exports.get(export_id)

    def get_latest_composition_export_record(
        self, composition_id: str
    ) -> CompositionExportRecordInfo | None:
        with self._database.snapshot():
            return self._composition_exports.get_latest(composition_id)

    def list_composition_export_records(
        self,
        *,
        limit: int = 200,
    ) -> tuple[CompositionExportRecordInfo, ...]:
        with self._database.snapshot():
            return self._composition_exports.list_all(limit=limit)

    def get_composition_output_execution(
        self,
        execution_id: str,
    ) -> CompositionOutputExecutionInfo:
        with self._database.snapshot():
            return self._composition_output_executions.get(execution_id)

    def list_active_composition_output_executions(
        self,
        *,
        limit: int = 20,
    ) -> tuple[CompositionOutputExecutionInfo, ...]:
        with self._database.snapshot():
            return self._composition_output_executions.list_active(limit=limit)

    def get_active_composition_output_execution(
        self,
    ) -> CompositionOutputExecutionInfo | None:
        active = self.list_active_composition_output_executions(limit=1)
        return active[0] if active else None

    def get_latest_export_record(
        self,
        point_set_id: str,
        asset_assignment_set_id: str,
    ) -> ExportRecordInfo | None:
        with self._database.snapshot():
            return self._exports.get_latest_for_inputs(
                point_set_id,
                asset_assignment_set_id,
            )

    def read_artifact_chunk(
        self,
        artifact_id: str,
        *,
        offset: int,
        limit: int,
    ) -> bytes:
        if offset < 0:
            raise ValueError("offset must be non-negative")
        if limit <= 0 or limit > 4 * 1024 * 1024:
            raise ValueError("limit must be between 1 and 4194304")
        with self.open_artifact(artifact_id) as stream:
            stream.seek(offset)
            return stream.read(limit)

    def get_scene_revision(self, scene_revision_id: str) -> SceneRevisionInfo:
        with self._database.snapshot():
            return self._scenes.get_scene_revision(scene_revision_id)

    def get_region_set(self, region_set_id: str) -> RegionSetInfo:
        with self._database.snapshot():
            return self._regions.get_region_set(region_set_id)

    def list_regions(self, region_set_id: str) -> tuple[RegionSummary, ...]:
        with self._database.snapshot():
            return self._regions.list_regions(region_set_id)

    def get_point_set(self, point_set_id: str) -> PointSetInfo:
        with self._database.snapshot():
            return self._points.get_point_set(point_set_id)

    def get_planting_run(self, planting_run_id: str) -> PlantingRunInfo:
        with self._database.snapshot():
            return self._points.get_planting_run(planting_run_id)

    def list_point_set_roles(
        self,
        point_set_id: str,
    ) -> tuple[PointRoleSummary, ...]:
        with self._database.snapshot():
            return self._points.list_point_set_roles(point_set_id)

    def get_assignment_set(
        self,
        assignment_set_id: str,
    ) -> AssetAssignmentSetInfo:
        with self._database.snapshot():
            return self._snapshots.get_assignment_set(assignment_set_id)

    def list_assignment_assets(
        self,
        assignment_set_id: str,
    ) -> tuple[AssetAssignmentSummary, ...]:
        with self._database.snapshot():
            return self._snapshots.list_assignment_assets(assignment_set_id)

    def get_effective_binding_snapshot(
        self,
        effective_binding_snapshot_id: str,
    ) -> EffectiveBindingSnapshotInfo:
        with self._database.snapshot():
            return self._snapshots.get_effective_binding_snapshot(
                effective_binding_snapshot_id
            )

    def get_target_asset_use_snapshot(
        self,
        target_asset_use_snapshot_id: str,
    ) -> TargetAssetUseSnapshotInfo:
        with self._database.snapshot():
            return self._snapshots.get_target_asset_use_snapshot(
                target_asset_use_snapshot_id
            )

    @contextmanager
    def open_artifact(self, artifact_id: str) -> Iterator[BinaryIO]:
        with self._database.snapshot():
            artifact = self._artifacts.get(artifact_id)
        with self._objects.open(artifact.content_hash) as stream:
            yield stream

    def get_plant_run_execution(
        self,
        execution_id: str,
    ) -> PlantRunExecutionInfo:
        with self._database.snapshot():
            return self._plant_run_executions.get(execution_id)

    def get_material_flow_execution(
        self,
        execution_id: str,
    ) -> MaterialFlowExecutionInfo:
        with self._database.snapshot():
            return self._material_flow_executions.get(execution_id)

    def get_scene_preparation(
        self,
        execution_id: str,
    ) -> ScenePreparationInfo:
        with self._database.snapshot():
            return self._scene_preparations.get(execution_id)

    def recent_scene_preparations(
        self,
        *,
        limit: int = 20,
    ) -> tuple[ScenePreparationInfo, ...]:
        with self._database.snapshot():
            return self._scene_preparations.list_recent(limit=limit)

    def recent_material_flow_executions(
        self,
        *,
        limit: int = 20,
    ) -> tuple[MaterialFlowExecutionInfo, ...]:
        with self._database.snapshot():
            return self._material_flow_executions.list_recent(limit=limit)

    def recent_plant_run_executions(
        self,
        *,
        limit: int = 20,
    ) -> tuple[PlantRunExecutionInfo, ...]:
        with self._database.snapshot():
            return self._plant_run_executions.list_recent(limit=limit)
