export interface StrategySummary {
  library_entry_id: string;
  strategy_id: string;
  strategy_version: string;
  package_hash: string;
  display_name: string;
  trust_state: string | null;
  executable: boolean;
}

export interface StrategyRole {
  role_key: string;
  display_name: string;
  description: string;
  required: boolean;
  assignment_policy: string;
  guide?: import("@verdflow/role-guide-ui").RoleGuide | null;
}

export interface StrategyGenerationParameter {
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

export interface ProfileSummary {
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

export interface StrategyOption {
  summary: StrategySummary;
  description: string;
  roles: StrategyRole[];
  default_random_seed: number | null;
  generation_parameters: StrategyGenerationParameter[];
  profiles: ProfileSummary[];
  parameter_preview?: import("@verdflow/strategy-preview-ui").StrategyPreviewSample | null;
}

export interface CatalogSummary {
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

export interface AssetInfo {
  binding_candidate?: MappingCandidate | null;
  source_catalog_name?: string | null;
  ordinal: number;
  asset_id: string;
  display_name: string;
  thumbnail_resource_id: string | null;
  lod: Record<string, unknown>;
  provider_metadata: Record<string, unknown>;
  suggested_scale: ScalePolicy | null;
  reference_radius_m: number | null;
  reference_height_m: number | null;
  tags: string[];
  collection_ids: string[];
}

export interface CatalogCollection {
  collection_id: string;
  display_name: string;
  asset_count: number;
}

export interface CatalogAssetPage {
  total_count: number;
  collections: CatalogCollection[];
  items: AssetInfo[];
}

export interface ProfileDetail {
  assets?: AssetInfo[];
  summary: ProfileSummary;
  role_count: number;
  assignments: AssignmentMap;
  catalog_drift: Record<string, string>;
  drift_count: number;
  strategy_parameters: Record<string, number> | null;
}

export interface SaveProfileRequest {
  profile_id?: string;
  source_profile_id?: string;
  display_name: string;
  strategy_id: string;
  strategy_version: string;
  catalog_id: string | null;
  assignments: AssignmentMap;
  expected_generation?: number;
  strategy_parameters?: Record<string, number> | null;
}

export interface ConfigurationOptions {
  strategies: StrategyOption[];
  catalogs: CatalogSummary[];
}

export type ScalePolicy =
  | { mode: "fixed"; value: number }
  | { mode: "uniform_random"; min: number; max: number };

export interface MappingCandidate {
  asset_id: string;
  weight: number;
  scale: ScalePolicy;
  display_name_snapshot?: string;
  target_asset_ref?: { target_type: string; provider_id: string; locator: Record<string, unknown> };
  reference_radius_m?: number;
  reference_height_m?: number;
  catalog_link?: {
    catalog_id: string;
    asset_id: string;
    observed_target_hash: string;
    observed_metadata_hash: string;
  };
}

export type AssignmentMap = Record<
  string,
  { enabled?: boolean; candidates: MappingCandidate[] }
>;

export interface ExecutionSnapshot {
  strategy_package_hash: string;
  strategy_id: string;
  strategy_version: string;
  target_type: string;
  asset_source_kind: "catalog" | "profile" | "draft";
  asset_source_id: string;
  asset_source_generation: number | null;
  asset_source_hash: string;
  profile_id: string | null;
  random_seed: number | null;
  generation_args: Record<string, unknown> | null;
}

export type OperationStage =
  | "selection_pending"
  | "selection_ready"
  | "elevation_pending"
  | "elevation_ready"
  | "delivery_pending"
  | "completed"
  | "cancelled"
  | "superseded"
  | "failed";

export interface SessionSnapshot {
  connector_session_id: string;
  generation_token: number;
  operation_id: string | null;
  stage: OperationStage | null;
  execution: ExecutionSnapshot | null;
  pending_request_id: string | null;
  pending_command_kind: CommandKind | null;
  selection_coordinate_count: number;
  elevation_point_count: number;
  export_id: string | null;
  error: string | null;
  selection_regions: LiveRegion[];
  sampling_failures?: { region_id: string; name: string; reason: string }[];
}

export interface LiveRegion {
  region_id: string;
  name: string;
  coordinates: number[][];
  bbox: number[];
}

export interface ElevationBatch {
  region_id: string;
  name: string;
  bbox: number[];
  start: number;
  count: number;
}

export interface Correlation {
  connector_session_id: string;
  operation_id: string;
  generation_token: number;
  request_id: string;
}

export type CommandKind =
  | "begin_polygon_selection"
  | "sample_elevation"
  | "import_xml";

export interface LiveCommand {
  sequence: number;
  kind: CommandKind;
  correlation: Correlation;
  payload: Record<string, unknown>;
}

export interface SelectionConfiguration {
  strategy_package_hash: string;
  profile_id?: string;
  catalog_id?: string;
  assignments?: AssignmentMap;
  random_seed?: number;
  generation_args?: Record<string, unknown>;
}
