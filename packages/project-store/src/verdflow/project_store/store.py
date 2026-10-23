"""Public ProjectStore facade."""

from __future__ import annotations

import os
import json
import shutil
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, Iterator

from verdflow.contracts import (
    ArtifactRef,
    AssetAssignmentSetInfo,
    AssetAssignmentSummary,
    TargetAssetUseSnapshotInfo,
    ClaimedJob,
    CoordinateFrameInfo,
    CoordinateReferenceKind,
    CompositionExportInputInfo,
    CompositionExportRecordInfo,
    CompositionOutputExecutionInfo,
    EffectiveBindingSnapshotInfo,
    ExportRecordInfo,
    GarbageCollectionResult,
    JobAttemptInfo,
    JobErrorInfo,
    JobEvent,
    JobInfo,
    JobLease,
    JobRetentionClass,
    JobRecoveryResult,
    JobRetentionSweepResult,
    JobSubmission,
    MaterialFlowExecutionInfo,
    ScenePreparationInfo,
    PlantingBatchInfo,
    PlantingCompositionInfo,
    PlantingRunInfo,
    PlantRunExecutionInfo,
    PointRoleSummary,
    PointSetInfo,
    ProjectInfo,
    ProjectWorkflowState,
    RegionSetInfo,
    RegionSummary,
    SceneRevisionInfo,
    SourceAssemblyMemberInfo,
    SourceAssemblyMemberSpec,
    SourceInfo,
    SourceKind,
    StrategyPackageSnapshotInfo,
    WorkerProcessInfo,
    WorkerProcessStatus,
)

from .artifacts import ArtifactRepository
from .composition_exports import CompositionExportRepository
from .composition_output_executions import CompositionOutputExecutionRepository
from .database import ProjectDatabase
from .errors import (
    ArtifactNotFoundError,
    ProjectAlreadyExistsError,
    ProjectNotFoundError,
    ProjectRecoveryError,
    SourceAssemblyError,
)
from .exports import ExportRepository
from .integrity import ProjectIntegrityChecker
from .jobs import JobRepository
from .layout import ProjectLayout
from .locking import ProjectLock, RecoveryGuard
from .material_flow_executions import MaterialFlowExecutionRepository
from .scene_preparation_executions import ScenePreparationRepository
from .migrations import (
    cleanup_completed_migration_backups,
    migrate_to_current,
)
from .object_store import ContentAddressedObjectStore
from .plant_run_executions import PlantRunExecutionRepository
from .planting_compositions import PlantingCompositionRepository
from .points import PointRepository
from .processes import ProcessInspector, SystemProcessInspector
from .projects import ProjectRepository
from .regions import RegionRepository
from .retention import RetentionRepository
from .scenes import SceneRepository
from .scenes import normalize_affine_matrix
from .snapshots import ProjectSnapshotRepository
from .recovery import (
    ProjectRecoveryCoordinator,
    RecoveryAudit,
    RecoveryOutcome,
    RecoveryPolicy,
)
from .schema import initialize_schema
from .storage import ProjectStorageProfile
from .worker_processes import WorkerProcessRepository
from .workflow import ProjectWorkflowRepository


class ProjectStore:
    """Coordinates project persistence without exposing storage internals."""

    def __init__(
        self,
        root: Path,
        *,
        storage_profile: ProjectStorageProfile = ProjectStorageProfile.DURABLE,
        process_inspector: ProcessInspector | None = None,
    ) -> None:
        self._layout = ProjectLayout.from_root(root)
        self._database = ProjectDatabase(
            self._layout.database,
            storage_profile=storage_profile,
        )
        self._lock = ProjectLock(self._layout.lock)
        self._process_inspector = process_inspector or SystemProcessInspector()
        self._writer_instance_id = f"core_{uuid.uuid4().hex}"
        self._object_store = ContentAddressedObjectStore(self._layout)
        self._artifacts = ArtifactRepository(self._database)
        self._jobs = JobRepository(self._database)
        self._projects = ProjectRepository(self._database)
        self._scenes = SceneRepository(self._database)
        self._regions = RegionRepository(self._database)
        self._retention = RetentionRepository(self._database)
        self._points = PointRepository(self._database)
        self._snapshots = ProjectSnapshotRepository(self._database)
        self._exports = ExportRepository(self._database)
        self._composition_exports = CompositionExportRepository(self._database)
        self._composition_output_executions = (
            CompositionOutputExecutionRepository(self._database)
        )
        self._material_flow_executions = MaterialFlowExecutionRepository(
            self._database
        )
        self._scene_preparations = ScenePreparationRepository(self._database)
        self._plant_run_executions = PlantRunExecutionRepository(self._database)
        self._planting_compositions = PlantingCompositionRepository(
            self._database
        )
        self._worker_processes = WorkerProcessRepository(self._database)
        self._workflow = ProjectWorkflowRepository(self._database)

    @classmethod
    def create(
        cls,
        root: Path,
        name: str,
        *,
        storage_profile: ProjectStorageProfile = ProjectStorageProfile.DURABLE,
        process_inspector: ProcessInspector | None = None,
    ) -> ProjectStore:
        store = cls(
            root,
            storage_profile=storage_profile,
            process_inspector=process_inspector,
        )
        if store.root.exists():
            raise ProjectAlreadyExistsError(str(store.root))
        store._layout.prepare()
        try:
            identity = store._process_inspector.current_identity()
            with RecoveryGuard(store._layout.recovery_guard):
                store._lock.acquire(identity, store._writer_instance_id)
            store._database.open()
            initialize_schema(store._database, name)
            ProjectIntegrityChecker(
                store._database,
                store._layout,
            ).verify()
        except Exception:
            store.close()
            store._layout.cleanup_failed_creation()
            raise
        return store

    @classmethod
    def open(
        cls,
        root: Path,
        *,
        storage_profile: ProjectStorageProfile = ProjectStorageProfile.DURABLE,
        recovery_policy: RecoveryPolicy = RecoveryPolicy.SAFE,
        process_inspector: ProcessInspector | None = None,
    ) -> ProjectStore:
        store = cls(
            root,
            storage_profile=storage_profile,
            process_inspector=process_inspector,
        )
        if not store._layout.database.is_file():
            raise ProjectNotFoundError(str(store._layout.database))
        if store._layout.recovery_blocker.is_file():
            raise ProjectRecoveryError(
                "Project has an unresolved failed recovery marker"
            )
        store._layout.prepare()
        recovery: RecoveryOutcome | None = None
        coordinator: ProjectRecoveryCoordinator | None = None
        try:
            coordinator = ProjectRecoveryCoordinator(
                store._layout,
                store._process_inspector,
                store._writer_instance_id,
            )
            recovery = coordinator.acquire_writer_lock(
                store._lock,
                recovery_policy,
            )
            store._database.open()
            migrate_to_current(
                store._database,
                store._layout.migration_backups,
            )
            integrity = ProjectIntegrityChecker(
                store._database,
                store._layout,
            ).verify(verify_content_hashes=recovery is not None)
            cleanup_completed_migration_backups(
                store._layout.migration_backups
            )
            if recovery is not None:
                collection = store.collect_garbage()
                RecoveryAudit(
                    store._database,
                    store._layout,
                ).record_success(
                    recovery,
                    checked_object_count=integrity.checked_object_count,
                    verified_content_hash_count=(
                        integrity.verified_content_hash_count
                    ),
                    removed_object_count=collection.removed_object_count,
                    removed_staging_file_count=(
                        collection.removed_staging_file_count
                    ),
                )
        except Exception as error:
            blocker_written = False
            if recovery is not None:
                try:
                    RecoveryAudit(
                        store._database,
                        store._layout,
                    ).record_failure(recovery, error)
                    blocker_written = True
                except Exception:
                    pass
            store.close()
            if recovery is not None and coordinator is not None:
                try:
                    restored = coordinator.restore_previous_lock(
                        store._lock,
                        recovery,
                    )
                    if restored and blocker_written:
                        RecoveryAudit(
                            store._database,
                            store._layout,
                        ).clear_blocker()
                except Exception:
                    pass
            raise
        return store

    def __enter__(self) -> ProjectStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def root(self) -> Path:
        return self._layout.root

    @property
    def objects_path(self) -> Path:
        return self._layout.objects

    @property
    def staging_path(self) -> Path:
        return self._layout.staging

    def close(self) -> None:
        try:
            self._database.close()
        finally:
            self._lock.release()

    def project_info(self) -> ProjectInfo:
        return self._projects.get_info()

    def project_workflow_state(self) -> ProjectWorkflowState:
        return self._workflow.get()

    def current_planting_composition(
        self,
    ) -> PlantingCompositionInfo | None:
        return self._planting_compositions.get_current()

    def list_planting_batches(self) -> tuple[PlantingBatchInfo, ...]:
        return self._planting_compositions.list_batches()

    def commit_current_planting_batch(
        self,
        *,
        plant_run_execution_id: str,
        selected_region_ids: tuple[str, ...],
        selected_asset_ids: tuple[str, ...] | None,
        continue_planting: bool,
        replacing_batch_id: str | None = None,
    ) -> tuple[PlantingCompositionInfo, PlantingBatchInfo]:
        return self._planting_compositions.commit_batch(
            plant_run_execution_id=plant_run_execution_id,
            selected_region_ids=selected_region_ids,
            selected_asset_ids=selected_asset_ids,
            continue_planting=continue_planting,
            replacing_batch_id=replacing_batch_id,
            created_at=self._now_text(),
        )

    def remove_planting_batch(self, batch_id: str) -> None:
        self._planting_compositions.remove_batch(
            batch_id,
            updated_at=self._now_text(),
        )

    def discard_current_planting_preview(
        self,
        plant_run_execution_id: str,
    ) -> None:
        self._planting_compositions.discard_current_preview(
            plant_run_execution_id,
            updated_at=self._now_text(),
        )

    def start_scene_preparation_workflow(
        self,
        execution: ScenePreparationInfo,
    ) -> ProjectWorkflowState:
        return self._workflow.start_scene_preparation(
            execution,
            updated_at=self._now_text(),
        )

    def synchronize_scene_preparation_workflow(
        self,
        execution: ScenePreparationInfo,
    ) -> ProjectWorkflowState:
        return self._workflow.synchronize_scene_preparation(
            execution,
            updated_at=self._now_text(),
        )

    def start_material_flow_workflow(
        self,
        execution: MaterialFlowExecutionInfo,
    ) -> ProjectWorkflowState:
        return self._workflow.start_material_flow(
            execution,
            updated_at=self._now_text(),
        )

    def synchronize_material_flow_workflow(
        self,
        execution: MaterialFlowExecutionInfo,
    ) -> ProjectWorkflowState:
        return self._workflow.synchronize_material_flow(
            execution,
            updated_at=self._now_text(),
        )

    def activate_region_source_workflow(
        self,
        *,
        source_id: str,
        region_set_id: str,
    ) -> ProjectWorkflowState:
        return self._workflow.activate_region_source(
            source_id=source_id,
            region_set_id=region_set_id,
            updated_at=self._now_text(),
        )

    def start_plant_run_workflow(
        self,
        execution: PlantRunExecutionInfo,
        *,
        region_set_id: str,
    ) -> ProjectWorkflowState:
        return self._workflow.start_plant_run(
            execution,
            region_set_id=region_set_id,
            updated_at=self._now_text(),
        )

    def create_coordinate_frame(
        self,
        *,
        name: str,
        handedness: str,
        up_axis: str,
        linear_unit: str,
        crs_kind: CoordinateReferenceKind,
        crs_definition: str | None = None,
        local_origin_x: float = 0.0,
        local_origin_y: float = 0.0,
        local_origin_z: float = 0.0,
    ) -> CoordinateFrameInfo:
        return self._scenes.create_coordinate_frame(
            name=name,
            handedness=handedness,
            up_axis=up_axis,
            linear_unit=linear_unit,
            crs_kind=crs_kind,
            crs_definition=crs_definition,
            local_origin_x=local_origin_x,
            local_origin_y=local_origin_y,
            local_origin_z=local_origin_z,
            created_at=self._now_text(),
        )

    def get_coordinate_frame(
        self,
        coordinate_frame_id: str,
    ) -> CoordinateFrameInfo:
        return self._scenes.get_coordinate_frame(coordinate_frame_id)

    def register_source(
        self,
        *,
        kind: SourceKind,
        original_name: str,
        artifact_id: str,
    ) -> SourceInfo:
        normalized_kind = SourceKind(kind)
        if normalized_kind is SourceKind.ASSEMBLY:
            raise ValueError(
                "Use create_source_assembly for Assembly Sources"
            )
        return self._scenes.create_source(
            kind=normalized_kind,
            original_name=original_name,
            artifact_id=artifact_id,
            imported_at=self._now_text(),
        )

    def get_source(self, source_id: str) -> SourceInfo:
        return self._scenes.get_source(source_id)

    def create_source_assembly(
        self,
        *,
        original_name: str,
        members: tuple[SourceAssemblyMemberSpec, ...],
    ) -> SourceInfo:
        normalized_name = original_name.strip()
        if not normalized_name:
            raise SourceAssemblyError("original_name is required")
        if not members:
            raise SourceAssemblyError(
                "Source Assembly requires at least one member"
            )
        manifest_members = []
        normalized_members = []
        for ordinal, member in enumerate(members):
            source = self._scenes.get_source(member.source_id)
            if source.kind is SourceKind.ASSEMBLY:
                raise SourceAssemblyError(
                    "Nested Source Assemblies are not supported"
                )
            matrix = normalize_affine_matrix(
                member.source_to_scene_matrix_row_major
            )
            normalized_members.append(
                SourceAssemblyMemberSpec(
                    source_id=source.source_id,
                    source_to_scene_matrix_row_major=matrix,
                )
            )
            manifest_members.append(
                {
                    "ordinal": ordinal,
                    "source_kind": source.kind.value,
                    "source_content_hash": source.content_hash,
                    "source_to_scene_matrix_row_major": list(matrix),
                }
            )
        manifest = {
            "schema": "verdflow.source-assembly",
            "version": "1",
            "members": manifest_members,
        }
        artifact = self.put_bytes(
            json.dumps(
                manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8"),
            media_type="application/vnd.verdflow.source-assembly+json",
            schema_id="verdflow.source-assembly",
            schema_version="1",
        )
        return self._scenes.create_source_assembly(
            original_name=normalized_name,
            artifact_id=artifact.artifact_id,
            members=tuple(normalized_members),
            imported_at=self._now_text(),
        )

    def list_source_assembly_members(
        self,
        source_id: str,
    ) -> tuple[SourceAssemblyMemberInfo, ...]:
        return self._scenes.list_source_assembly_members(source_id)

    def create_scene_revision(
        self,
        *,
        source_id: str,
        importer_id: str,
        importer_version: str,
        coordinate_frame_id: str,
        scene_manifest_artifact_id: str,
        parent_revision_id: str | None = None,
        activate: bool = True,
    ) -> SceneRevisionInfo:
        now = self._now_text()
        revision = self._scenes.create_scene_revision(
            source_id=source_id,
            parent_revision_id=parent_revision_id,
            importer_id=importer_id,
            importer_version=importer_version,
            coordinate_frame_id=coordinate_frame_id,
            scene_manifest_artifact_id=scene_manifest_artifact_id,
            created_at=now,
            activate=activate,
        )
        return revision

    def get_scene_revision(
        self,
        scene_revision_id: str,
    ) -> SceneRevisionInfo:
        return self._scenes.get_scene_revision(scene_revision_id)

    def list_scene_revisions(self) -> tuple[SceneRevisionInfo, ...]:
        return self._scenes.list_scene_revisions()

    def find_scene_revision_by_manifest(
        self,
        scene_manifest_artifact_id: str,
    ) -> SceneRevisionInfo | None:
        return self._scenes.find_scene_revision_by_manifest(
            scene_manifest_artifact_id
        )

    def set_active_scene_revision(
        self,
        scene_revision_id: str | None,
    ) -> ProjectInfo:
        return self._projects.set_active_scene_revision(
            scene_revision_id,
            self._now_text(),
        )

    def create_region_set(
        self,
        *,
        scene_revision_id: str,
        producer_job_id: str,
        extractor_id: str,
        extractor_version: str,
        input_hash: str,
        coordinate_frame_id: str,
        geometry_artifact_id: str,
        metrics_artifact_id: str,
        regions: tuple[RegionSummary, ...],
    ) -> RegionSetInfo:
        return self._regions.create_region_set(
            scene_revision_id=scene_revision_id,
            producer_job_id=producer_job_id,
            extractor_id=extractor_id,
            extractor_version=extractor_version,
            input_hash=input_hash,
            coordinate_frame_id=coordinate_frame_id,
            geometry_artifact_id=geometry_artifact_id,
            metrics_artifact_id=metrics_artifact_id,
            regions=regions,
            created_at=self._now_text(),
        )

    def get_region_set(self, region_set_id: str) -> RegionSetInfo:
        return self._regions.get_region_set(region_set_id)

    def get_region_set_by_job(
        self,
        producer_job_id: str,
    ) -> RegionSetInfo | None:
        return self._regions.get_region_set_by_job(producer_job_id)

    def list_regions(
        self,
        region_set_id: str,
    ) -> tuple[RegionSummary, ...]:
        return self._regions.list_regions(region_set_id)

    def create_generated_point_set(
        self,
        *,
        scene_revision_id: str,
        region_set_id: str,
        producer_job_id: str,
        strategy_id: str,
        strategy_version: str,
        strategy_package_hash: str,
        generator_id: str,
        generator_version: str,
        random_seed: int,
        input_hash: str,
        coordinate_frame_id: str,
        point_artifact_id: str,
        point_count: int,
        bounds: tuple[float, float, float, float, float, float] | None,
        roles: tuple[PointRoleSummary, ...],
    ) -> tuple[PlantingRunInfo, PointSetInfo]:
        return self._points.create_generated_point_set(
            scene_revision_id=scene_revision_id,
            region_set_id=region_set_id,
            producer_job_id=producer_job_id,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            strategy_package_hash=strategy_package_hash,
            generator_id=generator_id,
            generator_version=generator_version,
            random_seed=random_seed,
            input_hash=input_hash,
            coordinate_frame_id=coordinate_frame_id,
            point_artifact_id=point_artifact_id,
            point_count=point_count,
            bounds=bounds,
            roles=roles,
            created_at=self._now_text(),
        )

    def create_elevation_point_set(
        self,
        *,
        parent_point_set_id: str,
        producer_job_id: str,
        producer_id: str,
        producer_version: str,
        input_hash: str,
        coordinate_frame_id: str,
        point_artifact_id: str,
        resolution_policy: str,
        point_count: int,
        hit_count: int,
        miss_count: int,
        bounds: tuple[float, float, float, float, float, float] | None,
        roles: tuple[PointRoleSummary, ...],
    ) -> PointSetInfo:
        return self._points.create_elevation_point_set(
            parent_point_set_id=parent_point_set_id,
            producer_job_id=producer_job_id,
            producer_id=producer_id,
            producer_version=producer_version,
            input_hash=input_hash,
            coordinate_frame_id=coordinate_frame_id,
            point_artifact_id=point_artifact_id,
            resolution_policy=resolution_policy,
            point_count=point_count,
            hit_count=hit_count,
            miss_count=miss_count,
            bounds=bounds,
            roles=roles,
            created_at=self._now_text(),
        )

    def get_planting_run(
        self,
        planting_run_id: str,
    ) -> PlantingRunInfo:
        return self._points.get_planting_run(planting_run_id)

    def get_planting_run_by_job(
        self,
        producer_job_id: str,
    ) -> PlantingRunInfo | None:
        return self._points.get_planting_run_by_job(producer_job_id)

    def get_point_set(self, point_set_id: str) -> PointSetInfo:
        return self._points.get_point_set(point_set_id)

    def get_point_set_by_job(
        self,
        producer_job_id: str,
    ) -> PointSetInfo | None:
        return self._points.get_point_set_by_job(producer_job_id)

    def list_point_sets(
        self,
        planting_run_id: str,
    ) -> tuple[PointSetInfo, ...]:
        return self._points.list_point_sets(planting_run_id)

    def list_point_set_roles(
        self,
        point_set_id: str,
    ) -> tuple[PointRoleSummary, ...]:
        return self._points.list_point_set_roles(point_set_id)

    def create_strategy_package_snapshot(
        self,
        *,
        planting_run_id: str,
        package_artifact_id: str,
    ) -> StrategyPackageSnapshotInfo:
        return self._snapshots.create_strategy_package_snapshot(
            planting_run_id=planting_run_id,
            package_artifact_id=package_artifact_id,
            created_at=self._now_text(),
        )

    def get_strategy_package_snapshot(
        self,
        strategy_package_snapshot_id: str,
    ) -> StrategyPackageSnapshotInfo:
        return self._snapshots.get_strategy_package_snapshot(
            strategy_package_snapshot_id
        )

    def get_strategy_package_snapshot_by_run(
        self,
        planting_run_id: str,
    ) -> StrategyPackageSnapshotInfo | None:
        return self._snapshots.get_strategy_package_snapshot_by_run(
            planting_run_id
        )

    def create_target_asset_use_snapshot(
        self,
        *,
        target_type: str,
        provider_id: str,
        source_kind: str,
        source_id: str,
        source_generation: int | None,
        source_hash: str,
        selected_asset_count: int,
        semantic_hash: str,
        snapshot_artifact_id: str,
    ) -> TargetAssetUseSnapshotInfo:
        return self._snapshots.create_target_asset_use_snapshot(
            target_type=target_type,
            provider_id=provider_id,
            source_kind=source_kind,
            source_id=source_id,
            source_generation=source_generation,
            source_hash=source_hash,
            selected_asset_count=selected_asset_count,
            semantic_hash=semantic_hash,
            snapshot_artifact_id=snapshot_artifact_id,
            created_at=self._now_text(),
        )

    def get_target_asset_use_snapshot(
        self,
        target_asset_use_snapshot_id: str,
    ) -> TargetAssetUseSnapshotInfo:
        return self._snapshots.get_target_asset_use_snapshot(
            target_asset_use_snapshot_id
        )

    def create_effective_binding_snapshot(
        self,
        *,
        strategy_package_snapshot_id: str,
        target_asset_use_snapshot_id: str,
        target_type: str,
        source_profile_id: str | None,
        source_profile_hash: str | None,
        override_applied: bool,
        semantic_hash: str,
        snapshot_artifact_id: str,
    ) -> EffectiveBindingSnapshotInfo:
        return self._snapshots.create_effective_binding_snapshot(
            strategy_package_snapshot_id=strategy_package_snapshot_id,
            target_asset_use_snapshot_id=target_asset_use_snapshot_id,
            target_type=target_type,
            source_profile_id=source_profile_id,
            source_profile_hash=source_profile_hash,
            override_applied=override_applied,
            semantic_hash=semantic_hash,
            snapshot_artifact_id=snapshot_artifact_id,
            created_at=self._now_text(),
        )

    def get_effective_binding_snapshot(
        self,
        effective_binding_snapshot_id: str,
    ) -> EffectiveBindingSnapshotInfo:
        return self._snapshots.get_effective_binding_snapshot(
            effective_binding_snapshot_id
        )

    def create_asset_assignment_set(
        self,
        *,
        point_set_id: str,
        effective_binding_snapshot_id: str,
        producer_job_id: str,
        target_type: str,
        resolver_id: str,
        resolver_version: str,
        random_seed: int,
        semantic_hash: str,
        assignment_artifact_id: str,
        point_count: int,
        assets: tuple[AssetAssignmentSummary, ...],
    ) -> AssetAssignmentSetInfo:
        return self._snapshots.create_assignment_set(
            point_set_id=point_set_id,
            effective_binding_snapshot_id=effective_binding_snapshot_id,
            producer_job_id=producer_job_id,
            target_type=target_type,
            resolver_id=resolver_id,
            resolver_version=resolver_version,
            random_seed=random_seed,
            semantic_hash=semantic_hash,
            assignment_artifact_id=assignment_artifact_id,
            point_count=point_count,
            assets=assets,
            created_at=self._now_text(),
        )

    def get_asset_assignment_set(
        self,
        asset_assignment_set_id: str,
    ) -> AssetAssignmentSetInfo:
        return self._snapshots.get_assignment_set(asset_assignment_set_id)

    def get_asset_assignment_set_by_job(
        self,
        producer_job_id: str,
    ) -> AssetAssignmentSetInfo | None:
        return self._snapshots.get_assignment_set_by_job(producer_job_id)

    def list_asset_assignment_summaries(
        self,
        asset_assignment_set_id: str,
    ) -> tuple[AssetAssignmentSummary, ...]:
        return self._snapshots.list_assignment_assets(
            asset_assignment_set_id
        )

    def create_export_record(
        self,
        *,
        producer_job_id: str,
        adapter_id: str,
        adapter_version: str,
        target_type: str,
        point_set_id: str,
        asset_assignment_set_id: str,
        output_artifact_id: str,
        offset: tuple[float, float, float],
        record_count: int,
        selected_asset_ids: tuple[str, ...] | None = None,
        require_terrain_hit: bool = False,
    ) -> ExportRecordInfo:
        if not isinstance(require_terrain_hit, bool):
            raise ValueError("require_terrain_hit must be boolean")
        terrain_selected_count = None
        if require_terrain_hit:
            point_set = self.get_point_set(point_set_id)
            terrain_selected_count = point_set.hit_count
            if selected_asset_ids is not None:
                assignment_set = self.get_asset_assignment_set(asset_assignment_set_id)
                with self.open_artifact(point_set.point_artifact_id) as source:
                    points = json.load(source)
                with self.open_artifact(assignment_set.assignment_artifact_id) as source:
                    assignments = json.load(source)
                hit_ids = {point["id"] for point in points["points"]
                           if (point.get("terrain_sample") or {}).get("hit") is True}
                selected = set(selected_asset_ids)
                terrain_selected_count = sum(
                    row["point_id"] in hit_ids and row["asset_id"] in selected
                    for row in assignments["assignments"]
                )
        return self._exports.create(
            producer_job_id=producer_job_id,
            adapter_id=adapter_id,
            adapter_version=adapter_version,
            target_type=target_type,
            point_set_id=point_set_id,
            asset_assignment_set_id=asset_assignment_set_id,
            output_artifact_id=output_artifact_id,
            offset=offset,
            record_count=record_count,
            selected_asset_ids=selected_asset_ids,
            terrain_selected_count=terrain_selected_count,
            created_at=self._now_text(),
        )

    def get_export_record(self, export_id: str) -> ExportRecordInfo:
        return self._exports.get(export_id)

    def get_export_record_by_job(
        self,
        producer_job_id: str,
    ) -> ExportRecordInfo | None:
        return self._exports.get_by_job(producer_job_id)

    def get_latest_export_record(
        self,
        point_set_id: str,
        asset_assignment_set_id: str,
    ) -> ExportRecordInfo | None:
        return self._exports.get_latest_for_inputs(
            point_set_id,
            asset_assignment_set_id,
        )

    def create_composition_export_record(
        self,
        *,
        composition_id: str,
        composition_generation: int,
        producer_job_id: str,
        adapter_id: str,
        adapter_version: str,
        target_type: str,
        output_artifact_id: str,
        offset: tuple[float, float, float],
        record_count: int,
        inputs: tuple[CompositionExportInputInfo, ...],
    ) -> CompositionExportRecordInfo:
        return self._composition_exports.create(
            composition_id=composition_id,
            composition_generation=composition_generation,
            producer_job_id=producer_job_id,
            adapter_id=adapter_id,
            adapter_version=adapter_version,
            target_type=target_type,
            output_artifact_id=output_artifact_id,
            offset=offset,
            record_count=record_count,
            inputs=inputs,
            created_at=self._now_text(),
        )

    def get_composition_export_record(
        self, export_id: str
    ) -> CompositionExportRecordInfo:
        return self._composition_exports.get(export_id)

    def get_composition_export_record_by_job(
        self, producer_job_id: str
    ) -> CompositionExportRecordInfo | None:
        return self._composition_exports.get_by_job(producer_job_id)

    def get_latest_composition_export_record(
        self, composition_id: str
    ) -> CompositionExportRecordInfo | None:
        return self._composition_exports.get_latest(composition_id)

    def list_composition_export_records(
        self,
        *,
        limit: int = 200,
    ) -> tuple[CompositionExportRecordInfo, ...]:
        return self._composition_exports.list_all(limit=limit)

    def delete_composition_export_record(
        self,
        export_id: str,
    ) -> CompositionExportRecordInfo:
        return self._composition_exports.delete(export_id)

    def create_composition_output_execution(
        self,
        *,
        idempotency_key: str,
        request_artifact_id: str,
        request_hash: str,
        composition_id: str,
        composition_generation: int,
    ) -> CompositionOutputExecutionInfo:
        return self._composition_output_executions.create(
            idempotency_key=idempotency_key,
            request_artifact_id=request_artifact_id,
            request_hash=request_hash,
            composition_id=composition_id,
            composition_generation=composition_generation,
            created_at=self._now_text(),
        )

    def get_composition_output_execution(
        self,
        execution_id: str,
    ) -> CompositionOutputExecutionInfo:
        return self._composition_output_executions.get(execution_id)

    def list_active_composition_output_executions(
        self,
        *,
        limit: int = 20,
    ) -> tuple[CompositionOutputExecutionInfo, ...]:
        return self._composition_output_executions.list_active(limit=limit)

    def update_composition_output_execution(
        self,
        info: CompositionOutputExecutionInfo,
        *,
        expected_revision: int,
    ) -> CompositionOutputExecutionInfo:
        return self._composition_output_executions.update(
            info,
            expected_revision=expected_revision,
            updated_at=self._now_text(),
        )

    def create_plant_run_execution(
        self,
        *,
        idempotency_key: str,
        request_artifact_id: str,
        request_hash: str,
    ) -> PlantRunExecutionInfo:
        return self._plant_run_executions.create(
            idempotency_key=idempotency_key,
            request_artifact_id=request_artifact_id,
            request_hash=request_hash,
            created_at=self._now_text(),
        )

    def get_plant_run_execution(
        self,
        execution_id: str,
    ) -> PlantRunExecutionInfo:
        return self._plant_run_executions.get(execution_id)

    def get_plant_run_execution_by_idempotency(
        self,
        idempotency_key: str,
    ) -> PlantRunExecutionInfo | None:
        return self._plant_run_executions.get_by_idempotency(idempotency_key)

    def list_active_plant_run_executions(
        self,
        *,
        limit: int = 20,
    ) -> tuple[PlantRunExecutionInfo, ...]:
        return self._plant_run_executions.list_active(limit=limit)

    def update_plant_run_execution(
        self,
        info: PlantRunExecutionInfo,
        *,
        expected_revision: int,
    ) -> PlantRunExecutionInfo:
        return self._plant_run_executions.update(
            info,
            expected_revision=expected_revision,
            updated_at=self._now_text(),
        )

    def create_material_flow_execution(
        self,
        *,
        idempotency_key: str,
        request_artifact_id: str,
        request_hash: str,
    ) -> MaterialFlowExecutionInfo:
        return self._material_flow_executions.create(
            idempotency_key=idempotency_key,
            request_artifact_id=request_artifact_id,
            request_hash=request_hash,
            created_at=self._now_text(),
        )

    def get_material_flow_execution(
        self,
        execution_id: str,
    ) -> MaterialFlowExecutionInfo:
        return self._material_flow_executions.get(execution_id)

    def get_material_flow_execution_by_idempotency(
        self,
        idempotency_key: str,
    ) -> MaterialFlowExecutionInfo | None:
        return self._material_flow_executions.get_by_idempotency(idempotency_key)

    def list_active_material_flow_executions(
        self,
        *,
        limit: int = 20,
    ) -> tuple[MaterialFlowExecutionInfo, ...]:
        return self._material_flow_executions.list_active(limit=limit)

    def update_material_flow_execution(
        self,
        info: MaterialFlowExecutionInfo,
        *,
        expected_revision: int,
    ) -> MaterialFlowExecutionInfo:
        return self._material_flow_executions.update(
            info,
            expected_revision=expected_revision,
            updated_at=self._now_text(),
        )

    def create_scene_preparation(
        self,
        *,
        idempotency_key: str,
        request_artifact_id: str,
        request_hash: str,
        source_id: str,
    ) -> ScenePreparationInfo:
        return self._scene_preparations.create(
            idempotency_key=idempotency_key,
            request_artifact_id=request_artifact_id,
            request_hash=request_hash,
            source_id=source_id,
            created_at=self._now_text(),
        )

    def get_scene_preparation(
        self,
        execution_id: str,
    ) -> ScenePreparationInfo:
        return self._scene_preparations.get(execution_id)

    def get_scene_preparation_by_idempotency(
        self,
        idempotency_key: str,
    ) -> ScenePreparationInfo | None:
        return self._scene_preparations.get_by_idempotency(idempotency_key)

    def list_active_scene_preparations(
        self,
        *,
        limit: int = 20,
    ) -> tuple[ScenePreparationInfo, ...]:
        return self._scene_preparations.list_active(limit=limit)

    def update_scene_preparation(
        self,
        info: ScenePreparationInfo,
        *,
        expected_revision: int,
    ) -> ScenePreparationInfo:
        return self._scene_preparations.update(
            info,
            expected_revision=expected_revision,
            updated_at=self._now_text(),
        )

    def put_bytes(
        self,
        data: bytes,
        *,
        media_type: str,
        schema_id: str | None = None,
        schema_version: str | None = None,
    ) -> ArtifactRef:
        self._validate_media_type(media_type)
        stored_object = self._object_store.put_bytes(data)
        return self._artifacts.register(
            stored_object,
            media_type=media_type,
            schema_id=schema_id,
            schema_version=schema_version,
        )

    def put_file(
        self,
        source: Path,
        *,
        media_type: str,
        schema_id: str | None = None,
        schema_version: str | None = None,
    ) -> ArtifactRef:
        self._validate_media_type(media_type)
        stored_object = self._object_store.put_file(source)
        return self._artifacts.register(
            stored_object,
            media_type=media_type,
            schema_id=schema_id,
            schema_version=schema_version,
        )

    def get_artifact(self, artifact_id: str) -> ArtifactRef:
        return self._artifacts.get(artifact_id)

    @contextmanager
    def open_artifact(self, artifact_id: str) -> Iterator[BinaryIO]:
        artifact = self._artifacts.get(artifact_id)
        path = self._object_store.path_for(artifact.content_hash)
        if not path.is_file():
            raise ArtifactNotFoundError(
                f"{artifact_id}: object {artifact.content_hash} is missing"
            )
        with self._object_store.open(artifact.content_hash) as stream:
            yield stream

    def materialize_artifact(
        self,
        artifact_id: str,
        destination: Path,
    ) -> Path:
        """Copy an immutable Artifact to an Adapter-owned private path."""
        destination = destination.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(
            f".{destination.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            with self.open_artifact(artifact_id) as source:
                with temporary.open("wb") as target:
                    shutil.copyfileobj(source, target)
                    target.flush()
                    os.fsync(target.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination

    def delete_artifact(self, artifact_id: str) -> None:
        self._artifacts.delete(artifact_id)

    def collect_garbage(self) -> GarbageCollectionResult:
        result = self._object_store.collect_unreferenced(
            self._artifacts.referenced_hashes()
        )
        self._artifacts.delete_unreferenced_object_records()
        return GarbageCollectionResult(
            removed_object_count=result.removed_object_count,
            removed_byte_size=result.removed_byte_size,
            removed_staging_file_count=result.removed_staging_file_count,
        )

    def object_count(self) -> int:
        return self._artifacts.object_count()

    def artifact_count(self) -> int:
        return self._artifacts.artifact_count()

    def create_job(self, submission: JobSubmission, now: str) -> JobInfo:
        return self._jobs.create(submission, now)

    def get_job(self, job_id: str) -> JobInfo:
        return self._jobs.get(job_id)

    def promote_job_retention(
        self,
        job_id: str,
        retention_class: JobRetentionClass,
    ) -> JobInfo:
        return self._jobs.promote_retention(job_id, retention_class)

    def claim_next_job(
        self,
        *,
        worker_id: str,
        now: str,
        lease_expires_at: str,
        command_types: tuple[str, ...] | None = None,
    ) -> ClaimedJob | None:
        return self._jobs.claim_next(
            worker_id=worker_id,
            now=now,
            lease_expires_at=lease_expires_at,
            command_types=command_types,
        )

    def heartbeat_job(
        self,
        lease: JobLease,
        *,
        now: str,
        lease_expires_at: str,
        progress: float | None = None,
        stage: str | None = None,
    ) -> JobInfo:
        return self._jobs.heartbeat(
            lease,
            now=now,
            lease_expires_at=lease_expires_at,
            progress=progress,
            stage=stage,
        )

    def succeed_job(
        self,
        lease: JobLease,
        *,
        now: str,
        result_artifact_id: str | None = None,
    ) -> JobInfo:
        return self._jobs.succeed(
            lease,
            now=now,
            result_artifact_id=result_artifact_id,
        )

    def fail_job(
        self,
        lease: JobLease,
        *,
        now: str,
        error: JobErrorInfo,
    ) -> JobInfo:
        return self._jobs.fail(lease, now=now, error=error)

    def request_job_cancel(self, job_id: str, now: str) -> JobInfo:
        return self._jobs.request_cancel(job_id, now)

    def acknowledge_job_cancel(
        self,
        lease: JobLease,
        now: str,
    ) -> JobInfo:
        return self._jobs.acknowledge_cancel(lease, now)

    def recover_expired_jobs(self, now: str) -> JobRecoveryResult:
        return self._jobs.recover_expired(now)

    def sweep_expired_retention(
        self,
        *,
        now: str,
        orphan_before: str,
        limit: int = 100,
        collect_objects: bool = True,
        protected_artifact_ids: tuple[str, ...] = (),
    ) -> JobRetentionSweepResult:
        metadata = self._retention.sweep_expired(
            now=now,
            orphan_before=orphan_before,
            limit=limit,
            protected_artifact_ids=protected_artifact_ids,
        )
        garbage = self.collect_garbage() if collect_objects else None
        return JobRetentionSweepResult(
            examined_job_count=metadata.examined_job_count,
            removed_job_count=metadata.removed_job_count,
            protected_job_count=metadata.protected_job_count,
            removed_assignment_count=metadata.removed_assignment_count,
            removed_snapshot_count=metadata.removed_snapshot_count,
            removed_artifact_count=metadata.removed_artifact_count,
            removed_object_count=(
                garbage.removed_object_count if garbage is not None else 0
            ),
            removed_byte_size=(
                garbage.removed_byte_size if garbage is not None else 0
            ),
        )

    def list_job_events(
        self,
        job_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> tuple[JobEvent, ...]:
        return self._jobs.list_events(
            job_id,
            after_sequence=after_sequence,
            limit=limit,
        )

    def list_job_attempts(
        self,
        job_id: str,
    ) -> tuple[JobAttemptInfo, ...]:
        return self._jobs.list_attempts(job_id)

    def register_worker_process(
        self,
        process: WorkerProcessInfo,
    ) -> WorkerProcessInfo:
        return self._worker_processes.register(process)

    def heartbeat_worker_process(
        self,
        job_id: str,
        attempt_number: int,
        heartbeat_at: str,
    ) -> WorkerProcessInfo:
        return self._worker_processes.heartbeat(
            job_id,
            attempt_number,
            heartbeat_at,
        )

    def finish_worker_process(
        self,
        job_id: str,
        attempt_number: int,
        *,
        status: WorkerProcessStatus,
        finished_at: str,
        exit_code: int | None,
    ) -> WorkerProcessInfo:
        return self._worker_processes.finish(
            job_id,
            attempt_number,
            status=status,
            finished_at=finished_at,
            exit_code=exit_code,
        )

    def get_worker_process(
        self,
        job_id: str,
        attempt_number: int,
    ) -> WorkerProcessInfo:
        return self._worker_processes.get(job_id, attempt_number)

    def list_active_worker_processes(
        self,
    ) -> tuple[WorkerProcessInfo, ...]:
        return self._worker_processes.list_active()

    @staticmethod
    def _validate_media_type(media_type: str) -> None:
        if not media_type.strip():
            raise ValueError("media_type is required")

    @staticmethod
    def _now_text() -> str:
        return datetime.now(UTC).isoformat()
