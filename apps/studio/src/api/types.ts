import type { StrategyPreviewSample } from "@verdflow/strategy-preview-ui";
export type SystemHostState = "stopped" | "running" | "closing" | "failed";

export interface SystemHealth {
  state: SystemHostState;
  instance_id: string;
  system_root_id: string;
  library_available: boolean;
  active_project_count: number;
  model_provider_count: number;
  secret_store_kind: string;
  endpoint_base_url: string | null;
}

export type ProjectSessionState =
  | "starting"
  | "active"
  | "closing"
  | "closed"
  | "failed";

export interface ProjectSession {
  session_id: string;
  project_id: string;
  name: string;
  project_path: string;
  state: ProjectSessionState;
  opened_at: string;
  error: string | null;
}

export interface RecentProject {
  registry_id: string;
  project_id: string | null;
  name: string;
  project_path: string;
  last_opened_at: string;
  last_workspace: "dts" | "blender" | null;
  availability: "available" | "missing" | "unavailable";
  active_session_id: string | null;
  last_error: string | null;
}

export interface ProjectRemovalInfo {
  registry_id: string;
  project_id: string | null;
  project_path: string;
  removed: boolean;
  files_removed: boolean;
}

export type ProjectBusinessState =
  | "new"
  | "running"
  | "awaiting_material_confirmation"
  | "ready_to_plant"
  | "awaiting_point_review"
  | "completed"
  | "failed"
  | "cancelled";

export interface ProjectBusinessSummary {
  project_id: string;
  state: ProjectBusinessState;
  execution_kind: string | null;
  execution_id: string | null;
  execution_status: string | null;
  execution_stage: string | null;
  updated_at: string;
  source_id: string | null;
  scene_revision_id: string | null;
  scene_importer_id: string | null;
  scene_preparation_execution_id: string | null;
  material_flow_execution_id: string | null;
  region_set_id: string | null;
  plant_run_execution_id: string | null;
  error_code: string | null;
  error_message: string | null;
}

export interface PlantPreviewInputInfo {
  scene_revision_id: string;
  region_set_id: string;
  selected_region_ids: string[];
  strategy_package_hash: string;
  random_seed: number;
  target_type: string;
  catalog_id: string | null;
  profile_id: string | null;
  generation_args: Record<string, unknown>;
  binding_overrides: Record<string, Record<string, unknown>>;
}

export interface ItemCollection<Item> {
  items: Item[];
}

export type ProjectTaskStatus =
  | "queued"
  | "running"
  | "awaiting_user"
  | "cancelling"
  | "cancelled"
  | "succeeded"
  | "failed"
  | "interrupted";

export interface ProjectTaskInfo {
  job_id: string;
  business_name: string;
  status: ProjectTaskStatus;
  stage: string;
  progress: number;
  attempt_count: number;
  worker_id: string | null;
  heartbeat_at: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  error_code: string | null;
  error_message: string | null;
}

export interface ProjectTaskPage {
  items: ProjectTaskInfo[];
  next_cursor: string | null;
}

export interface ModelProviderInfo {
  provider_id: string;
  display_name: string;
  protocol: string;
  provider_version: string;
  default_model: string | null;
  endpoint: string;
  model_discovery_endpoint: string | null;
  model_discovery_supported: boolean;
  builtin: boolean;
  secret_configured: boolean;
  context_window_tokens: number | null;
  max_output_tokens: number | null;
  default_max_output_tokens: number;
  reasoning_mode: string;
}

export type ExternalRuntimePathKind = "file" | "directory";
export type ExternalRuntimeResolutionSource =
  | "manual"
  | "auto"
  | "unavailable";

export interface ExternalRuntimeInfo {
  runtime_id: string;
  display_name: string;
  path_kind: ExternalRuntimePathKind;
  configured_path: string | null;
  resolved_path: string | null;
  resolution_source: ExternalRuntimeResolutionSource;
  available: boolean;
  detail_code: string;
  required_names: string[];
}

export type DtsLiveServiceState =
  | "stopped"
  | "starting"
  | "waiting_for_explorer"
  | "connected"
  | "failed";

export interface DtsLiveServiceEvent {
  sequence: number;
  occurred_at: string;
  level: "info" | "warning" | "error";
  code: string;
  message: string;
}

export interface DtsLiveServiceStatus {
  state: DtsLiveServiceState;
  connector_base_url: string | null;
  connector_session_id: string | null;
  launch_url: string | null;
  launch_expires_at: string | null;
  explorer_executable: string | null;
  started_at: string | null;
  webui_connected: boolean;
  last_error: string | null;
  events: DtsLiveServiceEvent[];
}

export type ModelProviderProtocol =
  | "openai-responses"
  | "anthropic-messages"
  | "openai-chat-completions";

export type ModelProviderReasoningMode =
  | "provider_default"
  | "disabled"
  | "enabled";

export interface SaveModelProviderInput {
  display_name: string;
  protocol: ModelProviderProtocol;
  endpoint: string;
  model_discovery_endpoint: string | null;
  default_model: string;
  capabilities: {
    context_window_tokens: number | null;
    max_output_tokens: number | null;
    default_max_output_tokens: number;
    reasoning_mode: ModelProviderReasoningMode;
  };
}

export interface ModelProviderModelInfo {
  model_id: string;
  display_name: string | null;
  owned_by: string | null;
}

export interface ModelProviderModelList {
  provider_id: string;
  items: ModelProviderModelInfo[];
}

export interface ModelProviderTestResult {
  provider_id: string;
  model: string;
  succeeded: boolean;
  duration_ms: number;
  message: string;
}

export type SourceKind =
  | "assembly"
  | "fbx"
  | "blend"
  | "gltf"
  | "shapefile"
  | "geojson"
  | "geotiff"
  | "xml"
  | "other";

export interface SourceInfo {
  source_id: string;
  kind: SourceKind;
  original_name: string;
  artifact_id: string;
  content_hash: string;
  imported_at: string;
}

export interface JobSubmissionInfo {
  job_id: string;
  status: ProjectTaskStatus;
}

export interface RegionSourceFinalization {
  scene_revision: {
    scene_revision_id: string;
    source_id: string;
    importer_id: string;
  };
  region_set: {
    region_set_id: string;
    scene_revision_id: string;
    region_count: number;
  };
}

export type ScenePreparationStatus =
  | "queued"
  | "running"
  | "awaiting_confirmation"
  | "cancel_requested"
  | "cancelled"
  | "failed"
  | "succeeded";

export interface ScenePreparationInfo {
  execution_id: string;
  idempotency_key: string;
  request_artifact_id: string;
  request_hash: string;
  status: ScenePreparationStatus;
  stage: "inspection" | "material_flow" | "completed";
  revision: number;
  source_id: string;
  inspection_job_id: string | null;
  scene_revision_id: string | null;
  material_flow_execution_id: string | null;
  region_set_id: string | null;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export type MaterialFlowStatus = ScenePreparationStatus;

export interface MaterialFlowInfo {
  execution_id: string;
  idempotency_key: string;
  request_artifact_id: string;
  request_hash: string;
  status: MaterialFlowStatus;
  stage: "analysis" | "confirmation" | "extraction" | "completed";
  revision: number;
  analysis_job_id: string | null;
  rule_pack_artifact_id: string | null;
  extraction_job_id: string | null;
  region_set_id: string | null;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface MaterialTextureFact {
  image_name: string | null;
  source_relative_path: string | null;
}

export interface MaterialFactItem {
  material_name: string;
  face_count: number;
  object_count: number;
  object_names: string[];
  diffuse_color: number[];
  textures: MaterialTextureFact[];
}

export interface MaterialFactsView {
  scene_revision_id: string;
  materials: MaterialFactItem[];
}

export type MaterialClassification =
  | "general_green"
  | "street_tree_green"
  | "not_green";

export interface MaterialSelectionView {
  materials: Array<{
    material_name: string;
    classification: MaterialClassification;
  }>;
}

export interface MaterialClassificationItem {
  material_name: string;
  classification: MaterialClassification;
  confidence: number;
  evidence: string[];
}

export interface MaterialClassificationCandidate {
  schema: "verdflow.material-classification-candidate";
  version: "1";
  task_id: string;
  task_type: string;
  input_ref: string;
  invocation: {
    provider_id: string;
    provider_version: string;
    protocol: string;
    model: string;
    prompt_policy_version: string;
    request_id: string | null;
    usage: {
      input_tokens: number | null;
      output_tokens: number | null;
      total_tokens: number | null;
    };
  };
  materials: MaterialClassificationItem[];
  warnings: string[];
}

export interface Bounds3D {
  min_x: number;
  min_y: number;
  min_z: number;
  max_x: number;
  max_y: number;
  max_z: number;
}

export interface RegionClassificationSummary {
  classification: string | null;
  region_count: number;
  footprint_area_xy: number;
}

export interface RegionViewerManifest {
  region_set_id: string;
  scene_revision_id: string;
  coordinate_frame_id: string;
  region_count: number;
  bounds: Bounds3D | null;
  classifications: RegionClassificationSummary[];
}

export interface RegionViewerItem {
  region_id: string;
  ordinal: number;
  label: string;
  classification: string | null;
  strategy_hint: string | null;
  footprint_area_xy: number | null;
  bounds: Bounds3D;
  footprint_boundary_xyz: Array<Array<[number, number, number]>>;
}

export interface RegionViewportChunk {
  items: RegionViewerItem[];
  next_after_ordinal: number | null;
  truncated: boolean;
}

export interface StrategyLibrarySummary {
  library_entry_id: string;
  strategy_id: string;
  strategy_version: string;
  package_hash: string;
  display_name: string;
  trust_state: string | null;
  executable: boolean;
}

export interface StrategyRoleInfo {
  role_key: string;
  display_name: string;
  description: string;
  required: boolean;
  assignment_policy: string;
  guide?: import("@verdflow/role-guide-ui").RoleGuide | null;
}

export interface StrategyGenerationParameterInfo {
  key: string;
  value_type: "number";
  default: number;
  minimum: number;
  maximum: number;
  step: number;
  group: "quick" | "advanced";
  display_name: string;
  effect: string;
  visible_for_roles?: string[];
}

export interface StrategyLibraryDetail {
  summary: StrategyLibrarySummary;
  description: string;
  intent: Record<string, unknown>;
  terrain_suitability: Record<string, unknown>;
  roles: StrategyRoleInfo[];
  default_random_seed: number | null;
  generation_parameters: StrategyGenerationParameterInfo[];
  parameter_preview?: StrategyPreviewSample | null;
}

export type { StrategyPreviewSample, StrategyPreviewInput, StrategyPreviewResult, StrategyPreviewOperation, AssignmentPreviewInput, AssignmentPreviewResult } from "@verdflow/strategy-preview-ui";

export interface StrategyRemovalInfo {
  package_hash: string;
  removed: boolean;
}

export interface StrategyPackageImportInfo {
  source_path: string;
  package_hash: string;
  archive_hash: string;
  already_installed: boolean;
  strategy: StrategyLibrarySummary;
}

export interface StrategyPackageExportInfo {
  destination_path: string;
  package_hash: string;
  archive_hash: string;
  byte_size: number;
  strategy: StrategyLibrarySummary;
}

export interface AuthoringKitExportInfo {
  skill_id: "vfs-author" | "vfa-author";
  destination_path: string;
  archive_hash: string;
  byte_size: number;
}

export interface AssetCatalogSummary {
  catalog_entry_id: string;
  target_type: string;
  provider_id: string;
  catalog_id: string;
  display_name: string;
  generation: number;
  semantic_hash: string;
  created_at: string;
  updated_at: string;
  origin_package_hash: string | null;
}

export interface AssetPackageCatalogInfo extends AssetCatalogSummary {
  byte_size: number;
}

export interface AssetPackageImportInfo {
  source_path: string;
  package_hash: string;
  resource_count: number;
  already_installed: boolean;
  catalog: AssetPackageCatalogInfo;
}

export interface AssetPackageExportInfo {
  destination_path: string;
  package_hash: string;
  byte_size: number;
  resource_count: number;
  catalog: AssetPackageCatalogInfo;
}

export interface AssetCatalogAssetInfo {
  binding_candidate?: Record<string, unknown> | null;
  source_catalog_name?: string | null;
  ordinal: number;
  asset_id: string;
  display_name: string;
  thumbnail_resource_id: string | null;
  collection_ids: string[];
  lod: Record<string, unknown>;
  provider_metadata: Record<string, unknown>;
  suggested_scale: Record<string, unknown> | null;
  reference_radius_m: number | null;
  reference_height_m: number | null;
  tags: string[];
}

export interface AssetCatalogCollectionInfo {
  collection_id: string;
  display_name: string;
  asset_count: number;
}

export interface AssetCatalogAssetPage {
  catalog: AssetCatalogSummary;
  collections: AssetCatalogCollectionInfo[];
  total_count: number;
  items: AssetCatalogAssetInfo[];
  next_after_ordinal: number | null;
}

export type AssetScalePolicy =
  | { mode: "fixed"; value: number }
  | { mode: "uniform_random"; min: number; max: number };

export interface AssetCatalogDocumentAsset {
  asset_id: string;
  display_name: string;
  thumbnail?: string;
  lod?: Record<string, unknown>;
  provider_metadata: Record<string, unknown>;
  suggested_scale?: AssetScalePolicy;
  reference_radius_m?: number;
  reference_height_m?: number;
  tags?: string[];
  extensions?: Record<string, unknown>;
}

export interface AssetCatalogDocumentCollection {
  collection_id: string;
  display_name: string;
  asset_ids: string[];
}

export interface AssetCatalogDocument {
  schema: "verdflow.target-asset-catalog";
  version: "1";
  catalog_id: string;
  target_type: string;
  provider_id: string;
  display_name?: string;
  assets: AssetCatalogDocumentAsset[];
  collections?: AssetCatalogDocumentCollection[];
  extensions?: Record<string, unknown>;
}

export interface AssetCatalogInfo {
  catalog_entry_id: string;
  target_type: string;
  provider_id: string;
  catalog_id: string;
  display_name: string;
  generation: number;
  semantic_hash: string;
  byte_size: number;
  created_at: string;
  updated_at: string;
  origin_package_hash: string | null;
}

export interface AssetCatalogDetail {
  info: AssetCatalogInfo;
  document: AssetCatalogDocument;
}

export interface AssetCatalogRemovalInfo {
  target_type: string;
  catalog_id: string;
  removed: boolean;
  affected_profiles: number;
}

export interface LibraryResourcePayload {
  resource_id: string;
  content_hash: string;
  media_type: string;
  byte_size: number;
  created_at: string;
  data_base64: string;
}

export interface RoleBindingProfileSummary {
  profile_entry_id: string;
  profile_id: string;
  display_name: string;
  strategy_id: string;
  strategy_version: string;
  target_type: string;
  provider_id: string;
  catalog_id: string | null;
  generation: number;
  semantic_hash: string;
  updated_at: string;
  enabled_role_count?: number;
  asset_count?: number;
  plants?: Array<{ asset_id: string; display_name: string; thumbnail_resource_id: string | null }>;
  has_parameter_preset?: boolean;
  resolved_strategy_version?: string | null;
  resolved_strategy_hash?: string | null;
  strategy_resolution_issue?: string | null;
}

export interface RoleBindingProfileDetail {
  assets?: AssetCatalogAssetInfo[];
  summary: RoleBindingProfileSummary;
  role_count: number;
  assignments: Record<string, Record<string, unknown>>;
  catalog_drift: Record<string, "current" | "metadata_changed" | "target_changed" | "missing" | "unlinked">;
  drift_count: number;
  strategy_parameters: Record<string, number> | null;
}

export interface SaveRoleBindingProfileInput {
  profileId?: string | null;
  sourceProfileId?: string;
  displayName: string;
  strategyId: string;
  strategyVersion: string;
  targetType: string;
  catalogId?: string | null;
  assignments: Record<string, Record<string, unknown>>;
  expectedGeneration: number;
  strategyParameters?: Record<string, number> | null;
}

export interface RoleBindingProfileRemovalInfo {
  profile_id: string;
  removed: boolean;
}

export interface ProfilePackageImportInfo {
  source_path: string;
  package_hash: string;
  already_installed: boolean;
  profile: RoleBindingProfileSummary;
}

export interface ProfilePackageExportInfo {
  destination_path: string;
  package_hash: string;
  byte_size: number;
  profile: RoleBindingProfileSummary;
}

export type PlantRunExecutionStatus =
  | "queued"
  | "running"
  | "awaiting_confirmation"
  | "cancel_requested"
  | "cancelled"
  | "failed"
  | "succeeded";

export type PlantRunExecutionStage =
  | "generation"
  | "assignment"
  | "review"
  | "elevation"
  | "export"
  | "completed";

export interface PlantRunExecutionInfo {
  execution_id: string;
  idempotency_key: string;
  request_artifact_id: string;
  request_hash: string;
  continuation_artifact_id: string | null;
  continuation_hash: string | null;
  status: PlantRunExecutionStatus;
  stage: PlantRunExecutionStage;
  revision: number;
  generation_job_id: string | null;
  generated_point_set_id: string | null;
  assignment_job_id: string | null;
  asset_assignment_set_id: string | null;
  elevation_job_id: string | null;
  elevation_point_set_id: string | null;
  export_job_id: string | null;
  export_id: string | null;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface PlantPreviewSubmission {
  execution: PlantRunExecutionInfo;
  random_seed: number;
}

export interface PlantRunCancellationInfo {
  execution: PlantRunExecutionInfo;
  accepted: boolean;
  worker_signal_sent: boolean;
}

export interface PointLegendItem {
  key: string;
  point_count: number;
  display_name: string | null;
  reference_radius_m: number | null;
  reference_height_m: number | null;
  max_resolved_radius_m: number | null;
}

export interface PointReviewManifest {
  point_set_id: string;
  assignment_set_id: string;
  planting_run_id: string;
  region_set_id: string;
  coordinate_frame_id: string;
  kind: string;
  target_type: string;
  point_count: number;
  bounds: Bounds3D | null;
  roles: PointLegendItem[];
  assets: PointLegendItem[];
  max_resolved_radius_m: number;
}

export interface PointReviewItem {
  point_id: string;
  ordinal: number;
  region_id: string;
  role_key: string;
  position: [number, number, number];
  asset_id: string;
  target_scale_xyz: [number, number, number];
  group_id: string | null;
}

export interface PointViewportChunk {
  items: PointReviewItem[];
  next_after_ordinal: number | null;
  truncated: boolean;
}

export interface ExportDownloadInfo {
  selected_asset_ids: string[] | null;
  export_id: string;
  artifact_id: string;
  media_type: string;
  byte_size: number;
  suggested_filename: string;
  offset: [number, number, number];
}

export interface PlantPreviewReexportInfo {
  execution_id: string;
  export_job_id: string;
  status:
    | "queued"
    | "running"
    | "awaiting_user"
    | "cancelling"
    | "cancelled"
    | "succeeded"
    | "failed"
    | "interrupted";
  export_id: string | null;
  error_message: string | null;
}

export interface PlantingBatchSummary {
  batch_id: string;
  ordinal: number;
  plant_run_execution_id: string;
  generated_point_set_id: string;
  asset_assignment_set_id: string;
  selected_region_ids: string[];
  selected_asset_ids: string[] | null;
  strategy_id: string;
  strategy_version: string;
  point_count: number;
  status: PlantRunExecutionStatus;
  stage: PlantRunExecutionStage;
  export_id: string | null;
  error_message: string | null;
  created_at: string;
}

export interface PlantingCompositionSummary {
  composition_id: string;
  scene_revision_id: string;
  region_set_id: string;
  generation: number;
  occupied_region_ids: string[];
  batches: PlantingBatchSummary[];
  updated_at: string;
}

export interface PlantingCompositionEnvelope {
  composition: PlantingCompositionSummary | null;
}

export interface PlantingCompositionExportBatchInput {
  batch_id: string;
  selected_asset_ids: string[] | null;
}

export interface CompositionExportOperationInfo {
  operation_id: string;
  composition_id: string;
  composition_generation: number;
  stage: "elevation" | "export" | "completed";
  export_job_id: string | null;
  status: ProjectTaskStatus;
  export_id: string | null;
  error_message: string | null;
}

export interface CompositionExportOperationEnvelope {
  operation: CompositionExportOperationInfo | null;
}

export interface ProjectXmlOutputInfo {
  export_id: string;
  suggested_filename: string;
  media_type: string;
  byte_size: number;
  record_count: number;
  offset: [number, number, number];
  created_at: string;
}

export interface ProjectXmlOutputPage {
  items: ProjectXmlOutputInfo[];
}

export interface ApiErrorDocument {
  error?: {
    code?: string;
    message?: string;
  };
}
