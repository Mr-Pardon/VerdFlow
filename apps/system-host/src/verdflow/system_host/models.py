"""Public immutable state for the system-scoped host."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from verdflow.contracts import (
    StrategyPreviewSample,
    AssetCatalogInfo,
    JobInfo,
    JobStatus,
    MaterialFlowExecutionInfo,
    PlantRunExecutionInfo,
    PlantRunExecutionStage,
    PlantRunExecutionStatus,
    ScenePreparationInfo,
)


class SystemHostState(StrEnum):
    STOPPED = "stopped"
    RUNNING = "running"
    CLOSING = "closing"
    FAILED = "failed"


class ProjectSessionState(StrEnum):
    STARTING = "starting"
    ACTIVE = "active"
    CLOSING = "closing"
    CLOSED = "closed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class RecentProjectInfo:
    registry_id: str
    project_id: str | None
    name: str
    project_path: str
    last_opened_at: str
    last_workspace: str | None
    availability: str
    active_session_id: str | None
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class ProjectRemovalInfo:
    registry_id: str
    project_id: str | None
    project_path: str
    removed: bool
    files_removed: bool


class ProjectBusinessState(StrEnum):
    NEW = "new"
    RUNNING = "running"
    AWAITING_MATERIAL_CONFIRMATION = "awaiting_material_confirmation"
    READY_TO_PLANT = "ready_to_plant"
    AWAITING_POINT_REVIEW = "awaiting_point_review"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ProjectBusinessSummary:
    project_id: str
    state: ProjectBusinessState
    execution_kind: str | None
    execution_id: str | None
    execution_status: str | None
    execution_stage: str | None
    updated_at: str
    source_id: str | None = None
    scene_revision_id: str | None = None
    scene_importer_id: str | None = None
    scene_preparation_execution_id: str | None = None
    material_flow_execution_id: str | None = None
    region_set_id: str | None = None
    plant_run_execution_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class ProjectTaskInfo:
    job_id: str
    business_name: str
    status: str
    stage: str
    progress: float
    attempt_count: int
    worker_id: str | None
    heartbeat_at: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    error_code: str | None
    error_message: str | None


@dataclass(frozen=True, slots=True)
class ProjectTaskPage:
    items: tuple[ProjectTaskInfo, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class SystemHostConfig:
    library_path: Path
    system_path: Path | None = None
    project_start_timeout_seconds: float = 30.0
    project_call_timeout_seconds: float = 30.0
    project_idle_cycle_seconds: float = 0.1
    provider_test_timeout_seconds: float = 30.0

    def validate(self) -> None:
        if self.project_start_timeout_seconds <= 0:
            raise ValueError("project_start_timeout_seconds must be positive")
        if self.project_call_timeout_seconds <= 0:
            raise ValueError("project_call_timeout_seconds must be positive")
        if self.project_idle_cycle_seconds <= 0:
            raise ValueError("project_idle_cycle_seconds must be positive")
        if self.provider_test_timeout_seconds <= 0:
            raise ValueError("provider_test_timeout_seconds must be positive")

    def resolved_system_path(self) -> Path:
        return (
            self.system_path.resolve()
            if self.system_path is not None
            else (self.library_path.resolve().parent / "system-config")
        )


@dataclass(frozen=True, slots=True)
class ProjectSessionInfo:
    session_id: str
    project_id: str
    name: str
    project_path: str
    state: ProjectSessionState
    opened_at: str
    error: str | None = None


@dataclass(frozen=True, slots=True)
class SystemHostStatus:
    state: SystemHostState
    instance_id: str
    system_root_id: str
    library_path: str
    library_available: bool
    active_project_count: int
    model_provider_count: int
    secret_store_kind: str
    endpoint_base_url: str | None


@dataclass(frozen=True, slots=True)
class ModelProviderInfo:
    provider_id: str
    display_name: str
    protocol: str
    provider_version: str
    default_model: str | None
    endpoint: str
    model_discovery_endpoint: str | None
    model_discovery_supported: bool
    builtin: bool
    secret_configured: bool
    context_window_tokens: int | None
    max_output_tokens: int | None
    default_max_output_tokens: int
    reasoning_mode: str


@dataclass(frozen=True, slots=True)
class ModelProviderTestResult:
    provider_id: str
    model: str
    succeeded: bool
    duration_ms: int
    message: str


@dataclass(frozen=True, slots=True)
class ModelProviderModelInfo:
    model_id: str
    display_name: str | None
    owned_by: str | None


@dataclass(frozen=True, slots=True)
class ModelProviderModelList:
    provider_id: str
    items: tuple[ModelProviderModelInfo, ...]


@dataclass(frozen=True, slots=True)
class ExternalRuntimeInfo:
    runtime_id: str
    display_name: str
    path_kind: str
    configured_path: str | None
    resolved_path: str | None
    resolution_source: str
    available: bool
    detail_code: str
    required_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DtsLiveConfigurationRequest:
    strategy_package_hash: str
    profile_id: str | None = None
    catalog_id: str | None = None
    assignments: dict[str, dict[str, Any]] = field(default_factory=dict)
    random_seed: int | None = None
    generation_args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StrategyLibrarySummary:
    library_entry_id: str
    strategy_id: str
    strategy_version: str
    package_hash: str
    display_name: str
    trust_state: str | None
    executable: bool


@dataclass(frozen=True, slots=True)
class StrategyRoleDiagramMarkInfo:
    role_key: str
    kind: str
    x: float
    y: float
    size: float
    points: tuple[tuple[float, float], ...] = ()


@dataclass(frozen=True, slots=True)
class StrategyRoleGuideInfo:
    group_id: str
    group_order: int
    group_name: str
    group_description: str
    suggested_assets: tuple[str, ...]
    note: str
    diagram: tuple[StrategyRoleDiagramMarkInfo, ...]


@dataclass(frozen=True, slots=True)
class StrategyRoleInfo:
    role_key: str
    display_name: str
    description: str
    required: bool
    assignment_policy: str
    guide: StrategyRoleGuideInfo | None = None


@dataclass(frozen=True, slots=True)
class StrategyGenerationParameterInfo:
    key: str
    value_type: str
    default: float
    minimum: float
    maximum: float
    step: float
    group: str
    display_name: str
    effect: str
    visible_for_roles: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StrategyLibraryDetail:
    summary: StrategyLibrarySummary
    description: str
    intent: dict[str, Any]
    terrain_suitability: dict[str, Any]
    roles: tuple[StrategyRoleInfo, ...]
    default_random_seed: int | None
    generation_parameters: tuple[StrategyGenerationParameterInfo, ...]
    parameter_preview: StrategyPreviewSample | None = None


@dataclass(frozen=True, slots=True)
class StrategyRemovalInfo:
    package_hash: str
    removed: bool


@dataclass(frozen=True, slots=True)
class StrategyPackageImportInfo:
    source_path: str
    package_hash: str
    archive_hash: str
    already_installed: bool
    strategy: StrategyLibrarySummary


@dataclass(frozen=True, slots=True)
class StrategyPackageExportInfo:
    destination_path: str
    package_hash: str
    archive_hash: str
    byte_size: int
    strategy: StrategyLibrarySummary


@dataclass(frozen=True, slots=True)
class AuthoringKitExportInfo:
    skill_id: str
    destination_path: str
    archive_hash: str
    byte_size: int


@dataclass(frozen=True, slots=True)
class AssetCatalogSummary:
    catalog_entry_id: str
    target_type: str
    provider_id: str
    catalog_id: str
    display_name: str
    generation: int
    semantic_hash: str
    created_at: str
    updated_at: str
    origin_package_hash: str | None


@dataclass(frozen=True, slots=True)
class AssetPackageImportInfo:
    source_path: str
    package_hash: str
    resource_count: int
    already_installed: bool
    catalog: AssetCatalogInfo


@dataclass(frozen=True, slots=True)
class AssetPackageExportInfo:
    destination_path: str
    package_hash: str
    byte_size: int
    resource_count: int
    catalog: AssetCatalogInfo


@dataclass(frozen=True, slots=True)
class ProfilePackageImportInfo:
    source_path: str
    package_hash: str
    already_installed: bool
    profile: "RoleBindingProfileSummary"


@dataclass(frozen=True, slots=True)
class ProfilePackageExportInfo:
    destination_path: str
    package_hash: str
    byte_size: int
    profile: "RoleBindingProfileSummary"


@dataclass(frozen=True, slots=True)
class AssetCatalogCollectionInfo:
    collection_id: str
    display_name: str
    asset_count: int


@dataclass(frozen=True, slots=True)
class AssetCatalogAssetInfo:
    ordinal: int
    asset_id: str
    display_name: str
    thumbnail_resource_id: str | None
    lod: dict[str, Any]
    provider_metadata: dict[str, Any]
    suggested_scale: dict[str, Any] | None
    tags: tuple[str, ...]
    collection_ids: tuple[str, ...]
    reference_radius_m: float | None = None
    reference_height_m: float | None = None
    binding_candidate: dict[str, Any] | None = None
    source_catalog_name: str | None = None


@dataclass(frozen=True, slots=True)
class AssetCatalogAssetPage:
    catalog: AssetCatalogSummary
    total_count: int
    collections: tuple[AssetCatalogCollectionInfo, ...]
    items: tuple[AssetCatalogAssetInfo, ...]
    next_after_ordinal: int | None


@dataclass(frozen=True, slots=True)
class AssetCatalogDetail:
    info: AssetCatalogInfo
    document: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AssetCatalogSaveRequest:
    document: dict[str, Any]
    expected_generation: int


@dataclass(frozen=True, slots=True)
class ProfilePlantPreview:
    asset_id: str
    display_name: str
    thumbnail_resource_id: str | None


@dataclass(frozen=True, slots=True)
class RoleBindingProfileSummary:
    profile_entry_id: str
    profile_id: str
    display_name: str
    strategy_id: str
    strategy_version: str
    target_type: str
    provider_id: str
    catalog_id: str | None
    generation: int
    semantic_hash: str
    updated_at: str
    enabled_role_count: int = 0
    asset_count: int = 0
    plants: tuple[ProfilePlantPreview, ...] = ()
    has_parameter_preset: bool = False
    resolved_strategy_version: str | None = None
    resolved_strategy_hash: str | None = None
    strategy_resolution_issue: str | None = None


@dataclass(frozen=True, slots=True)
class RoleBindingProfileDetail:
    summary: RoleBindingProfileSummary
    role_count: int
    assignments: dict[str, dict[str, Any]]
    catalog_drift: dict[str, str]
    drift_count: int
    strategy_parameters: dict[str, float] | None = None
    assets: tuple[AssetCatalogAssetInfo, ...] = ()


@dataclass(frozen=True, slots=True)
class RoleBindingProfileRemovalInfo:
    profile_id: str
    removed: bool


@dataclass(frozen=True, slots=True)
class RoleBindingProfileSaveRequest:
    display_name: str
    strategy_id: str
    strategy_version: str
    target_type: str
    catalog_id: str | None
    assignments: dict[str, dict[str, Any]]
    expected_generation: int = 0
    profile_id: str | None = None
    strategy_parameters: dict[str, float] | None = None
    replace_strategy_parameters: bool = False
    source_profile_id: str | None = None


@dataclass(frozen=True, slots=True)
class DtsLiveStrategyOption:
    summary: StrategyLibrarySummary
    description: str
    roles: tuple[StrategyRoleInfo, ...]
    default_random_seed: int | None
    generation_parameters: tuple[StrategyGenerationParameterInfo, ...]
    profiles: tuple[RoleBindingProfileSummary, ...]
    parameter_preview: StrategyPreviewSample | None = None


@dataclass(frozen=True, slots=True)
class DtsLiveConfigurationOptions:
    strategies: tuple[DtsLiveStrategyOption, ...]
    catalogs: tuple[AssetCatalogSummary, ...]


@dataclass(frozen=True, slots=True)
class PlantPreviewRequest:
    scene_revision_id: str
    region_set_id: str
    selected_region_ids: tuple[str, ...]
    strategy_package_hash: str
    target_type: str
    catalog_id: str | None
    random_seed: int | None = None
    profile_id: str | None = None
    assignment_random_seed: int | None = None
    generation_args: dict[str, Any] = field(default_factory=dict)
    binding_overrides: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PlantPreviewSubmission:
    execution: PlantRunExecutionInfo
    random_seed: int


@dataclass(frozen=True, slots=True)
class PlantPreviewInputInfo:
    scene_revision_id: str
    region_set_id: str
    selected_region_ids: tuple[str, ...]
    strategy_package_hash: str
    random_seed: int
    target_type: str
    catalog_id: str | None
    profile_id: str | None
    generation_args: dict[str, Any]
    binding_overrides: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PlantPreviewOutputRequest:
    offset: tuple[float, float, float] = (0.0, 0.0, 0.0)
    selected_asset_ids: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class PlantingCompositionExportBatchRequest:
    batch_id: str
    selected_asset_ids: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class PlantingCompositionExportRequest:
    offset: tuple[float, float, float] = (0.0, 0.0, 0.0)
    batches: tuple[PlantingCompositionExportBatchRequest, ...] = ()


@dataclass(frozen=True, slots=True)
class PlantPreviewReexportInfo:
    execution_id: str
    export_job_id: str
    status: JobStatus
    export_id: str | None
    error_message: str | None


@dataclass(frozen=True, slots=True)
class PlantingBatchSummary:
    batch_id: str
    ordinal: int
    plant_run_execution_id: str
    generated_point_set_id: str
    asset_assignment_set_id: str
    selected_region_ids: tuple[str, ...]
    selected_asset_ids: tuple[str, ...] | None
    strategy_id: str
    strategy_version: str
    point_count: int
    status: PlantRunExecutionStatus
    stage: PlantRunExecutionStage
    export_id: str | None
    error_message: str | None
    created_at: str


@dataclass(frozen=True, slots=True)
class PlantingCompositionSummary:
    composition_id: str
    scene_revision_id: str
    region_set_id: str
    generation: int
    occupied_region_ids: tuple[str, ...]
    batches: tuple[PlantingBatchSummary, ...]
    updated_at: str


@dataclass(frozen=True, slots=True)
class PlantingBatchCommitRequest:
    selected_asset_ids: tuple[str, ...] | None = None
    continue_planting: bool = False
    replacing_batch_id: str | None = None


@dataclass(frozen=True, slots=True)
class CompositionExportOperationInfo:
    operation_id: str
    composition_id: str
    composition_generation: int
    stage: str
    export_job_id: str | None
    status: JobStatus
    export_id: str | None
    error_message: str | None


@dataclass(frozen=True, slots=True)
class PlantExportRequest:
    planting: PlantPreviewRequest
    offset: tuple[float, float, float] = (0.0, 0.0, 0.0)


@dataclass(frozen=True, slots=True)
class PlantExportSubmission:
    execution: PlantRunExecutionInfo
    random_seed: int


@dataclass(frozen=True, slots=True)
class ExportDownloadInfo:
    export_id: str
    artifact_id: str
    media_type: str
    byte_size: int
    suggested_filename: str
    offset: tuple[float, float, float]
    selected_asset_ids: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class ProjectXmlOutputInfo:
    export_id: str
    suggested_filename: str
    media_type: str
    byte_size: int
    record_count: int
    offset: tuple[float, float, float]
    created_at: str


@dataclass(frozen=True, slots=True)
class JobCancellationInfo:
    job: JobInfo
    accepted: bool


@dataclass(frozen=True, slots=True)
class PlantRunCancellationInfo:
    execution: PlantRunExecutionInfo
    accepted: bool
    worker_signal_sent: bool


@dataclass(frozen=True, slots=True)
class MaterialFlowCancellationInfo:
    execution: MaterialFlowExecutionInfo
    accepted: bool
    worker_signal_sent: bool


@dataclass(frozen=True, slots=True)
class ScenePreparationCancellationInfo:
    execution: ScenePreparationInfo
    accepted: bool
    worker_signal_sent: bool
