import type { PlatformBridge } from "../platform";
import type { StarterManifest, StarterImportOperation } from "./starterResources";
import type {
  AssignmentPreviewInput,
  AssignmentPreviewResult,
  ApiErrorDocument,
  ItemCollection,
  MaterialClassification,
  MaterialClassificationCandidate,
  MaterialFactsView,
  MaterialSelectionView,
  MaterialFlowInfo,
  ModelProviderInfo,
  ModelProviderModelList,
  ModelProviderTestResult,
  ProjectBusinessSummary,
  ProjectRemovalInfo,
  ProjectSession,
  ProjectTaskPage,
  RecentProject,
  RegionViewerManifest,
  RegionViewportChunk,
  ScenePreparationInfo,
  SourceInfo,
  AssetCatalogAssetPage,
  AssetCatalogSummary,
  AssetPackageExportInfo,
  AssetPackageImportInfo,
  AuthoringKitExportInfo,
  AssetCatalogDocument,
  AssetCatalogDetail,
  AssetCatalogRemovalInfo,
  LibraryResourcePayload,
  PlantPreviewSubmission,
  PlantPreviewReexportInfo,
  PlantPreviewInputInfo,
  PlantingCompositionEnvelope,
  PlantingCompositionExportBatchInput,
  PlantingCompositionSummary,
  CompositionExportOperationInfo,
  CompositionExportOperationEnvelope,
  ProjectXmlOutputInfo,
  ProjectXmlOutputPage,
  PlantRunCancellationInfo,
  PlantRunExecutionInfo,
  PointReviewManifest,
  PointViewportChunk,
  ExportDownloadInfo,
  ExternalRuntimeInfo,
  DtsLiveServiceStatus,
  RoleBindingProfileDetail,
  RoleBindingProfileRemovalInfo,
  RoleBindingProfileSummary,
  ProfilePackageExportInfo,
  ProfilePackageImportInfo,
  SaveModelProviderInput,
  SaveRoleBindingProfileInput,
  StrategyLibraryDetail,
  StrategyPreviewInput,
  StrategyPreviewOperation,
  StrategyLibrarySummary,
  StrategyPackageExportInfo,
  StrategyPackageImportInfo,
  StrategyRemovalInfo,
  SystemHealth,
  SourceKind,
  JobSubmissionInfo,
  RegionSourceFinalization,
} from "./types";

const BLENDER_REGION_PROVIDER_ID = "verdflow-blender-worker";

export class LocalApiError extends Error {
  constructor(
    message: string,
    readonly status: number | null,
    readonly code: string,
  ) {
    super(message);
    this.name = "LocalApiError";
  }
}

export class LocalApiClient {
  constructor(private readonly bridge: PlatformBridge) {}

  health(): Promise<SystemHealth> {
    return this.request("/api/v1/health");
  }

  starterResources(): Promise<StarterManifest> {
    return this.request("/api/v1/starter-resources");
  }

  importStarterResources(revision: string): Promise<StarterImportOperation> {
    return this.request("/api/v1/starter-resources/import", { method: "POST", body: JSON.stringify({ revision }) });
  }

  starterImport(operationId: string): Promise<StarterImportOperation> {
    return this.request(`/api/v1/starter-resources/import?operation_id=${encodeURIComponent(operationId)}`);
  }

  cancelStarterImport(operationId: string): Promise<StarterImportOperation> {
    return this.request("/api/v1/starter-resources/import", { method: "DELETE", body: JSON.stringify({ operation_id: operationId }) });
  }

  activeProjects(): Promise<ItemCollection<ProjectSession>> {
    return this.request("/api/v1/projects");
  }

  recentProjects(): Promise<ItemCollection<RecentProject>> {
    return this.request("/api/v1/recent-projects");
  }

  deleteProject(registryId: string): Promise<ProjectRemovalInfo> {
    return this.request(
      `/api/v1/recent-projects/${encodeURIComponent(registryId)}`,
      { method: "DELETE" },
    );
  }

  modelProviders(): Promise<ItemCollection<ModelProviderInfo>> {
    return this.request("/api/v1/model-providers");
  }

  externalRuntimes(): Promise<ItemCollection<ExternalRuntimeInfo>> {
    return this.request("/api/v1/external-runtimes");
  }

  configureExternalRuntime(
    runtimeId: string,
    path: string,
  ): Promise<ExternalRuntimeInfo> {
    return this.request(
      `/api/v1/external-runtimes/${encodeURIComponent(runtimeId)}`,
      {
        method: "PUT",
        body: JSON.stringify({ path }),
      },
    );
  }

  clearExternalRuntime(runtimeId: string): Promise<ExternalRuntimeInfo> {
    return this.request(
      `/api/v1/external-runtimes/${encodeURIComponent(runtimeId)}`,
      { method: "DELETE" },
    );
  }

  dtsLiveStatus(): Promise<DtsLiveServiceStatus> {
    return this.request("/api/v1/dts-live/status");
  }

  startDtsLive(): Promise<DtsLiveServiceStatus> {
    return this.request("/api/v1/dts-live/start", { method: "POST" });
  }

  relaunchDtsLive(): Promise<DtsLiveServiceStatus> {
    return this.request("/api/v1/dts-live/relaunch", { method: "POST" });
  }

  stopDtsLive(): Promise<DtsLiveServiceStatus> {
    return this.request("/api/v1/dts-live/stop", { method: "POST" });
  }

  saveModelProvider(
    providerId: string,
    input: SaveModelProviderInput,
  ): Promise<ModelProviderInfo> {
    return this.request(
      `/api/v1/model-providers/${encodeURIComponent(providerId)}`,
      {
        method: "PUT",
        body: JSON.stringify(input),
      },
    );
  }

  setModelProviderSecret(
    providerId: string,
    apiKey: string,
  ): Promise<ModelProviderInfo> {
    return this.request(
      `/api/v1/model-providers/${encodeURIComponent(providerId)}/secret`,
      {
        method: "PUT",
        body: JSON.stringify({ api_key: apiKey }),
      },
    );
  }

  setModelProviderModel(
    providerId: string,
    model: string,
  ): Promise<ModelProviderInfo> {
    return this.request(
      `/api/v1/model-providers/${encodeURIComponent(providerId)}/model`,
      {
        method: "PUT",
        body: JSON.stringify({ model }),
      },
    );
  }

  modelProviderModels(providerId: string): Promise<ModelProviderModelList> {
    return this.request(
      `/api/v1/model-providers/${encodeURIComponent(providerId)}/models`,
      {
        method: "POST",
        body: JSON.stringify({}),
      },
    );
  }

  deleteModelProviderSecret(providerId: string): Promise<ModelProviderInfo> {
    return this.request(
      `/api/v1/model-providers/${encodeURIComponent(providerId)}/secret`,
      { method: "DELETE" },
    );
  }

  deleteModelProvider(providerId: string): Promise<ModelProviderInfo> {
    return this.request(
      `/api/v1/model-providers/${encodeURIComponent(providerId)}`,
      { method: "DELETE" },
    );
  }

  testModelProvider(
    providerId: string,
    model?: string,
  ): Promise<ModelProviderTestResult> {
    return this.request(
      `/api/v1/model-providers/${encodeURIComponent(providerId)}/test`,
      {
        method: "POST",
        body: JSON.stringify(model ? { model } : {}),
      },
    );
  }

  projectSummary(sessionId: string): Promise<ProjectBusinessSummary> {
    return this.request(
      `/api/v1/projects/${encodeURIComponent(sessionId)}/summary`,
    );
  }

  openProject(projectPath: string, workspace: "dts" | "blender" | null) {
    return this.request<ProjectSession>("/api/v1/projects", {
      method: "POST",
      body: JSON.stringify({
        action: "open",
        project_path: projectPath,
        workspace,
        enable_geospatial_worker: workspace !== "blender",
      }),
    });
  }

  createProject(input: {
    projectPath: string;
    projectName: string;
    enableModelApiWorker: boolean;
    enableBlenderWorker?: boolean;
    enableGeospatialWorker?: boolean;
  }): Promise<ProjectSession> {
    return this.request("/api/v1/projects", {
      method: "POST",
      body: JSON.stringify({
        action: "create",
        project_path: input.projectPath,
        project_name: input.projectName,
        enable_blender_worker: input.enableBlenderWorker ?? true,
        enable_geospatial_worker: input.enableGeospatialWorker ?? false,
        enable_model_api_worker: input.enableModelApiWorker,
        workspace: "dts",
      }),
    });
  }

  addFbxSource(sessionId: string, sourcePath: string): Promise<SourceInfo> {
    return this.addSource(sessionId, sourcePath, "fbx");
  }

  addSource(
    sessionId: string,
    sourcePath: string,
    kind: SourceKind,
  ): Promise<SourceInfo> {
    return this.request(
      `/api/v1/projects/${encodeURIComponent(sessionId)}/sources`,
      {
        method: "POST",
        body: JSON.stringify({ source_path: sourcePath, kind }),
      },
    );
  }

  createIdentitySourceAssembly(input: {
    sessionId: string;
    originalName: string;
    sourceIds: string[];
  }): Promise<SourceInfo> {
    return this.request(
      `/api/v1/projects/${encodeURIComponent(input.sessionId)}/source-assemblies`,
      {
        method: "POST",
        body: JSON.stringify({
          original_name: input.originalName,
          source_ids: input.sourceIds,
        }),
      },
    );
  }

  submitRegionSource(input: {
    sessionId: string;
    sourceId: string;
    idempotencyKey: string;
  }): Promise<JobSubmissionInfo> {
    return this.request(
      `/api/v1/projects/${encodeURIComponent(input.sessionId)}/region-sources`,
      {
        method: "POST",
        body: JSON.stringify({
          source_id: input.sourceId,
          provider_id: "verdflow.geospatial",
          idempotency_key: input.idempotencyKey,
        }),
      },
    );
  }

  finalizeRegionSource(
    sessionId: string,
    jobId: string,
  ): Promise<RegionSourceFinalization> {
    return this.request(
      `/api/v1/projects/${encodeURIComponent(sessionId)}/region-sources/${encodeURIComponent(jobId)}/finalize`,
      { method: "POST", body: "{}" },
    );
  }

  submitScenePreparation(input: {
    sessionId: string;
    sourceId: string;
    selectionMode: "ai_assisted" | "manual";
    confirmationMode: "automatic" | "manual";
    analysisProviderId: string | null;
    idempotencyKey: string;
  }): Promise<ScenePreparationInfo> {
    return this.request(
      `/api/v1/projects/${encodeURIComponent(input.sessionId)}/scene-preparations`,
      {
        method: "POST",
        body: JSON.stringify({
          source_id: input.sourceId,
          analysis_provider_id: input.analysisProviderId,
          region_provider_id: BLENDER_REGION_PROVIDER_ID,
          confirmation_mode: input.confirmationMode,
          selection_mode: input.selectionMode,
          idempotency_key: input.idempotencyKey,
        }),
      },
    );
  }

  scenePreparation(
    sessionId: string,
    executionId: string,
  ): Promise<ScenePreparationInfo> {
    return this.request(
      `/api/v1/projects/${encodeURIComponent(sessionId)}/scene-preparations/${encodeURIComponent(executionId)}`,
    );
  }

  projectTasks(sessionId: string, limit = 20): Promise<ProjectTaskPage> {
    const query = new URLSearchParams({ limit: String(limit) });
    return this.request(
      `/api/v1/projects/${encodeURIComponent(sessionId)}/tasks?${query}`,
    );
  }

  materialFlow(
    sessionId: string,
    executionId: string,
  ): Promise<MaterialFlowInfo> {
    return this.request(
      `/api/v1/projects/${encodeURIComponent(sessionId)}/material-flows/${encodeURIComponent(executionId)}`,
    );
  }

  materialFacts(
    sessionId: string,
    sceneRevisionId: string,
  ): Promise<MaterialFactsView> {
    return this.request(
      `/api/v1/projects/${encodeURIComponent(sessionId)}/scene-revisions/${encodeURIComponent(sceneRevisionId)}/material-facts`,
    );
  }

  currentMaterialSelection(
    sessionId: string,
  ): Promise<MaterialSelectionView> {
    return this.request(
      `/api/v1/projects/${encodeURIComponent(sessionId)}/current-material-selection`,
    );
  }

  materialCandidate(
    sessionId: string,
    analysisJobId: string,
  ): Promise<MaterialClassificationCandidate> {
    return this.request(
      `/api/v1/projects/${encodeURIComponent(sessionId)}/material-analyses/${encodeURIComponent(analysisJobId)}/candidate`,
    );
  }

  confirmScenePreparation(input: {
    sessionId: string;
    executionId: string;
    materials: Array<{
      material_name: string;
      classification: MaterialClassification;
    }>;
  }): Promise<ScenePreparationInfo> {
    return this.request(
      `/api/v1/projects/${encodeURIComponent(input.sessionId)}/scene-preparations/${encodeURIComponent(input.executionId)}/confirm`,
      {
        method: "POST",
        body: JSON.stringify({ materials: input.materials }),
      },
    );
  }

  reviseMaterials(input: {
    sessionId: string;
    materials: Array<{
      material_name: string;
      classification: MaterialClassification;
    }>;
    idempotencyKey: string;
  }): Promise<MaterialFlowInfo> {
    return this.request(
      `/api/v1/projects/${encodeURIComponent(input.sessionId)}/material-revisions`,
      {
        method: "POST",
        body: JSON.stringify({
          materials: input.materials,
          idempotency_key: input.idempotencyKey,
        }),
      },
    );
  }

  regionManifest(
    sessionId: string,
    regionSetId: string,
  ): Promise<RegionViewerManifest> {
    return this.request(
      `/api/v1/projects/${encodeURIComponent(sessionId)}/region-sets/${encodeURIComponent(regionSetId)}/viewer`,
    );
  }

  regionGeometry(input: {
    sessionId: string;
    regionSetId: string;
    minX: number;
    minY: number;
    maxX: number;
    maxY: number;
    classifications?: string[];
    afterOrdinal?: number;
    limit?: number;
    simplificationTolerance?: number;
  }): Promise<RegionViewportChunk> {
    const simplificationTolerance =
      input.simplificationTolerance ??
      Math.max(input.maxX - input.minX, input.maxY - input.minY) / 2000;
    const query = new URLSearchParams({
      min_x: String(input.minX),
      min_y: String(input.minY),
      max_x: String(input.maxX),
      max_y: String(input.maxY),
      after_ordinal: String(input.afterOrdinal ?? -1),
      limit: String(input.limit ?? 5000),
      simplification_tolerance: String(simplificationTolerance),
    });
    if (input.classifications?.length) {
      query.set("classification", input.classifications.join(","));
    }
    return this.request(
      `/api/v1/projects/${encodeURIComponent(input.sessionId)}/region-sets/${encodeURIComponent(input.regionSetId)}/geometry?${query}`,
    );
  }

  strategies(): Promise<ItemCollection<StrategyLibrarySummary>> {
    return this.request("/api/v1/strategies");
  }

  strategy(packageHash: string): Promise<StrategyLibraryDetail> {
    return this.request(
      `/api/v1/strategies/${encodeURIComponent(packageHash)}`,
    );
  }

  submitStrategyExample(input: StrategyPreviewInput): Promise<StrategyPreviewOperation> {
    return this.request("/api/v1/strategy-previews", { method: "POST", body: JSON.stringify(input) });
  }

  previewAssetAssignments(input: AssignmentPreviewInput): Promise<AssignmentPreviewResult> {
    return this.request("/api/v1/asset-assignment-previews", {
      method: "POST", body: JSON.stringify(input),
    });
  }

  strategyExample(previewId: string): Promise<StrategyPreviewOperation> {
    return this.request(`/api/v1/strategy-previews/${encodeURIComponent(previewId)}`);
  }

  cancelStrategyExample(previewId: string): Promise<StrategyPreviewOperation> {
    return this.request(`/api/v1/strategy-previews/${encodeURIComponent(previewId)}`, { method: "DELETE" });
  }

  installStrategy(sourcePath: string): Promise<StrategyLibraryDetail> {
    return this.request("/api/v1/strategies/import", {
      method: "POST",
      body: JSON.stringify({ source_path: sourcePath }),
    });
  }

  importStrategyPackage(sourcePath: string): Promise<StrategyPackageImportInfo> {
    return this.request("/api/v1/strategy-packages/import", {
      method: "POST",
      body: JSON.stringify({ source_path: sourcePath }),
    });
  }

  exportStrategyPackage(input: {
    packageHash: string;
    destinationPath: string;
  }): Promise<StrategyPackageExportInfo> {
    return this.request(
      `/api/v1/strategies/${encodeURIComponent(input.packageHash)}/export-package`,
      {
        method: "POST",
        body: JSON.stringify({ destination_path: input.destinationPath }),
      },
    );
  }

  exportAuthoringKit(input: {
    skillId: "vfs-author" | "vfa-author";
    destinationPath: string;
  }): Promise<AuthoringKitExportInfo> {
    return this.request(
      `/api/v1/authoring-kits/${encodeURIComponent(input.skillId)}/export`,
      {
        method: "POST",
        body: JSON.stringify({ destination_path: input.destinationPath }),
      },
    );
  }

  trustStrategy(packageHash: string): Promise<StrategyLibrarySummary> {
    return this.request(
      `/api/v1/strategies/${encodeURIComponent(packageHash)}/trust`,
      { method: "POST", body: JSON.stringify({}) },
    );
  }

  revokeStrategyTrust(packageHash: string): Promise<StrategyLibrarySummary> {
    return this.request(
      `/api/v1/strategies/${encodeURIComponent(packageHash)}/revoke`,
      { method: "POST", body: JSON.stringify({}) },
    );
  }

  uninstallStrategy(packageHash: string): Promise<StrategyRemovalInfo> {
    return this.request(
      `/api/v1/strategies/${encodeURIComponent(packageHash)}`,
      { method: "DELETE" },
    );
  }

  assetCatalogs(targetType: string): Promise<{
    target_type: string;
    items: AssetCatalogSummary[];
  }> {
    const query = new URLSearchParams({ target_type: targetType });
    return this.request(`/api/v1/asset-catalogs?${query}`);
  }

  searchAssets(input: import("@verdflow/asset-browser-ui").AssetSearchRequest): Promise<import("@verdflow/asset-browser-ui").AssetSearchPage<import("./types").AssetCatalogAssetInfo>> {
    return this.request("/api/v1/asset-search", { method: "POST", body: JSON.stringify(input) });
  }

  assetCatalogAssets(input: {
    targetType: string;
    catalogId: string;
    afterOrdinal?: number;
    limit?: number;
    collectionId?: string;
    query?: string;
  }): Promise<AssetCatalogAssetPage> {
    const query = new URLSearchParams({
      after_ordinal: String(input.afterOrdinal ?? -1),
      limit: String(input.limit ?? 200),
    });
    if (input.collectionId) query.set("collection_id", input.collectionId);
    if (input.query) query.set("query", input.query);
    return this.request(
      `/api/v1/asset-catalogs/${encodeURIComponent(input.targetType)}/${encodeURIComponent(input.catalogId)}/assets?${query}`,
    );
  }

  assetCatalog(
    targetType: string,
    catalogId: string,
  ): Promise<AssetCatalogDetail> {
    return this.request(
      `/api/v1/asset-catalogs/${encodeURIComponent(targetType)}/${encodeURIComponent(catalogId)}`,
    );
  }

  saveAssetCatalog(input: {
    targetType: string;
    catalogId: string;
    document: AssetCatalogDocument;
    expectedGeneration: number;
  }): Promise<AssetCatalogDetail> {
    return this.request(
      `/api/v1/asset-catalogs/${encodeURIComponent(input.targetType)}/${encodeURIComponent(input.catalogId)}`,
      {
        method: "PUT",
        body: JSON.stringify({
          document: input.document,
          expected_generation: input.expectedGeneration,
        }),
      },
    );
  }

  deleteAssetCatalog(input: {
    targetType: string;
    catalogId: string;
  }): Promise<AssetCatalogRemovalInfo> {
    return this.request(
      `/api/v1/asset-catalogs/${encodeURIComponent(input.targetType)}/${encodeURIComponent(input.catalogId)}`,
      { method: "DELETE" },
    );
  }

  importAssetCatalogPackage(
    sourcePath: string,
    replaceExisting = false,
  ): Promise<AssetPackageImportInfo> {
    return this.request("/api/v1/asset-catalog-packages/import", {
      method: "POST",
      body: JSON.stringify({
        source_path: sourcePath,
        replace_existing: replaceExisting,
      }),
    });
  }

  exportAssetCatalogPackage(input: {
    targetType: string;
    catalogId: string;
    destinationPath: string;
  }): Promise<AssetPackageExportInfo> {
    return this.request(
      `/api/v1/asset-catalogs/${encodeURIComponent(input.targetType)}/${encodeURIComponent(input.catalogId)}/export-package`,
      {
        method: "POST",
        body: JSON.stringify({ destination_path: input.destinationPath }),
      },
    );
  }

  libraryResource(resourceId: string): Promise<LibraryResourcePayload> {
    return this.request(
      `/api/v1/library-resources/${encodeURIComponent(resourceId)}`,
    );
  }

  importLibraryThumbnail(sourcePath: string): Promise<{
    resource_id: string;
    content_hash: string;
    media_type: string;
    byte_size: number;
    created_at: string;
  }> {
    return this.request("/api/v1/library-resources/import", {
      method: "POST",
      body: JSON.stringify({ source_path: sourcePath }),
    });
  }

  roleBindingProfiles(input: {
    targetType: string;
    strategyId?: string;
    strategyVersion?: string;
    catalogId?: string;
  }): Promise<{ target_type: string; items: RoleBindingProfileSummary[] }> {
    const query = new URLSearchParams({ target_type: input.targetType });
    if (input.strategyId) query.set("strategy_id", input.strategyId);
    if (input.strategyVersion) query.set("strategy_version", input.strategyVersion);
    if (input.catalogId) query.set("catalog_id", input.catalogId);
    return this.request(`/api/v1/role-binding-profiles?${query}`);
  }

  roleBindingProfile(
    profileId: string,
  ): Promise<RoleBindingProfileDetail> {
    return this.request(
      `/api/v1/role-binding-profiles/${encodeURIComponent(profileId)}`,
    );
  }

  deleteRoleBindingProfile(
    profileId: string,
  ): Promise<RoleBindingProfileRemovalInfo> {
    return this.request(
      `/api/v1/role-binding-profiles/${encodeURIComponent(profileId)}`,
      { method: "DELETE" },
    );
  }

  saveRoleBindingProfile(
    input: SaveRoleBindingProfileInput,
  ): Promise<RoleBindingProfileDetail> {
    return this.request("/api/v1/role-binding-profiles", {
      method: "POST",
      body: JSON.stringify({
        profile_id: input.profileId ?? null,
        source_profile_id: input.sourceProfileId ?? null,
        display_name: input.displayName,
        strategy_id: input.strategyId,
        strategy_version: input.strategyVersion,
        target_type: input.targetType,
        catalog_id: input.catalogId ?? null,
        assignments: input.assignments,
        ...(input.strategyParameters === undefined ? {} : { strategy_parameters: input.strategyParameters }),
        expected_generation: input.expectedGeneration,
      }),
    });
  }

  syncRoleBindingProfileAssets(
    profileId: string,
    expectedGeneration: number,
  ): Promise<RoleBindingProfileDetail> {
    return this.request(
      `/api/v1/role-binding-profiles/${encodeURIComponent(profileId)}/sync-catalog-metadata`,
      {
        method: "POST",
        body: JSON.stringify({ expected_generation: expectedGeneration }),
      },
    );
  }

  importRoleBindingProfilePackage(
    sourcePath: string,
  ): Promise<ProfilePackageImportInfo> {
    return this.request("/api/v1/role-binding-profile-packages/import", {
      method: "POST",
      body: JSON.stringify({ source_path: sourcePath }),
    });
  }

  exportRoleBindingProfilePackage(input: {
    profileId: string;
    destinationPath: string;
  }): Promise<ProfilePackageExportInfo> {
    return this.request(
      `/api/v1/role-binding-profiles/${encodeURIComponent(input.profileId)}/export-package`,
      {
        method: "POST",
        body: JSON.stringify({ destination_path: input.destinationPath }),
      },
    );
  }

  submitPlantPreview(input: {
    sessionId: string;
    sceneRevisionId: string;
    regionSetId: string;
    selectedRegionIds: string[];
    strategyPackageHash: string;
    catalogId?: string | null;
    profileId?: string | null;
    randomSeed?: number | null;
    generationArgs?: Record<string, unknown>;
    bindingOverrides?: Record<string, Record<string, unknown>>;
    idempotencyKey: string;
  }): Promise<PlantPreviewSubmission> {
    return this.request(
      `/api/v1/projects/${encodeURIComponent(input.sessionId)}/plant-previews`,
      {
        method: "POST",
        body: JSON.stringify({
          scene_revision_id: input.sceneRevisionId,
          region_set_id: input.regionSetId,
          selected_region_ids: input.selectedRegionIds,
          strategy_package_hash: input.strategyPackageHash,
          target_type: "dts",
          catalog_id: input.catalogId,
          profile_id: input.profileId ?? null,
          random_seed: input.randomSeed ?? null,
          generation_args: input.generationArgs ?? {},
          binding_overrides: input.bindingOverrides ?? {},
          idempotency_key: input.idempotencyKey,
        }),
      },
    );
  }

  plantPreview(
    sessionId: string,
    executionId: string,
  ): Promise<PlantRunExecutionInfo> {
    return this.request(
      `/api/v1/projects/${encodeURIComponent(sessionId)}/plant-previews/${encodeURIComponent(executionId)}`,
    );
  }

  cancelPlantRun(
    sessionId: string,
    executionId: string,
  ): Promise<PlantRunCancellationInfo> {
    return this.request(
      `/api/v1/projects/${encodeURIComponent(sessionId)}/plant-runs/${encodeURIComponent(executionId)}/cancel`,
      { method: "POST" },
    );
  }

  retryPlantPreviewAssignment(
    sessionId: string,
    executionId: string,
  ): Promise<PlantRunExecutionInfo> {
    return this.request(
      `/api/v1/projects/${encodeURIComponent(sessionId)}/plant-previews/${encodeURIComponent(executionId)}/retry-assignment`,
      {
        method: "POST",
        body: JSON.stringify({}),
      },
    );
  }

  plantPreviewInput(
    sessionId: string,
    executionId: string,
  ): Promise<PlantPreviewInputInfo> {
    return this.request(
      `/api/v1/projects/${encodeURIComponent(sessionId)}/plant-previews/${encodeURIComponent(executionId)}/input`,
    );
  }

  plantingComposition(
    sessionId: string,
  ): Promise<PlantingCompositionEnvelope> {
    return this.request(
      `/api/v1/projects/${encodeURIComponent(sessionId)}/planting-composition`,
    );
  }

  commitPlantingBatch(input: {
    sessionId: string;
    executionId: string;
    selectedAssetIds: string[] | null;
    continuePlanting: boolean;
    replacingBatchId?: string | null;
  }): Promise<PlantingCompositionSummary> {
    return this.request(
      `/api/v1/projects/${encodeURIComponent(input.sessionId)}/plant-previews/${encodeURIComponent(input.executionId)}/commit`,
      {
        method: "POST",
        body: JSON.stringify({
          selected_asset_ids: input.selectedAssetIds,
          continue_planting: input.continuePlanting,
          replacing_batch_id: input.replacingBatchId ?? null,
        }),
      },
    );
  }

  discardPlantPreview(input: {
    sessionId: string;
    executionId: string;
  }): Promise<PlantingCompositionEnvelope> {
    return this.request(
      `/api/v1/projects/${encodeURIComponent(input.sessionId)}/plant-previews/${encodeURIComponent(input.executionId)}`,
      { method: "DELETE" },
    );
  }

  removePlantingBatch(input: {
    sessionId: string;
    batchId: string;
  }): Promise<PlantingCompositionEnvelope> {
    return this.request(
      `/api/v1/projects/${encodeURIComponent(input.sessionId)}/planting-batches/${encodeURIComponent(input.batchId)}`,
      { method: "DELETE" },
    );
  }

  exportPlantingComposition(input: {
    sessionId: string;
    offset: [number, number, number];
    batches: PlantingCompositionExportBatchInput[];
  }): Promise<CompositionExportOperationInfo> {
    return this.request(
      `/api/v1/projects/${encodeURIComponent(input.sessionId)}/planting-composition-exports`,
      {
        method: "POST",
        body: JSON.stringify({
          offset: input.offset,
          batches: input.batches,
        }),
      },
    );
  }

  plantingCompositionExport(input: {
    sessionId: string;
    operationId: string;
  }): Promise<CompositionExportOperationInfo> {
    return this.request(
      `/api/v1/projects/${encodeURIComponent(input.sessionId)}/planting-composition-exports/${encodeURIComponent(input.operationId)}`,
    );
  }

  activePlantingCompositionExport(
    sessionId: string,
  ): Promise<CompositionExportOperationEnvelope> {
    return this.request(
      `/api/v1/projects/${encodeURIComponent(sessionId)}/planting-composition-exports/active`,
    );
  }

  projectXmlOutputs(
    sessionId: string,
  ): Promise<ProjectXmlOutputPage> {
    return this.request(
      `/api/v1/projects/${encodeURIComponent(sessionId)}/xml-outputs`,
    );
  }

  deleteProjectXmlOutput(input: {
    sessionId: string;
    exportId: string;
  }): Promise<ProjectXmlOutputInfo> {
    return this.request(
      `/api/v1/projects/${encodeURIComponent(input.sessionId)}/xml-outputs/${encodeURIComponent(input.exportId)}`,
      { method: "DELETE" },
    );
  }

  pointReviewManifest(input: {
    sessionId: string;
    pointSetId: string;
    assignmentSetId: string;
  }): Promise<PointReviewManifest> {
    const query = new URLSearchParams({
      assignment_set_id: input.assignmentSetId,
    });
    return this.request(
      `/api/v1/projects/${encodeURIComponent(input.sessionId)}/point-sets/${encodeURIComponent(input.pointSetId)}/review?${query}`,
    );
  }

  pointReviewPoints(input: {
    sessionId: string;
    pointSetId: string;
    assignmentSetId: string;
    minX: number;
    minY: number;
    maxX: number;
    maxY: number;
    roles?: string[];
    assets?: string[];
    afterOrdinal?: number;
    limit?: number;
  }): Promise<PointViewportChunk> {
    const query = new URLSearchParams({
      assignment_set_id: input.assignmentSetId,
      min_x: String(input.minX),
      min_y: String(input.minY),
      max_x: String(input.maxX),
      max_y: String(input.maxY),
      after_ordinal: String(input.afterOrdinal ?? -1),
      limit: String(input.limit ?? 20_000),
    });
    if (input.roles?.length) query.set("role", input.roles.join(","));
    if (input.assets?.length) query.set("asset", input.assets.join(","));
    return this.request(
      `/api/v1/projects/${encodeURIComponent(input.sessionId)}/point-sets/${encodeURIComponent(input.pointSetId)}/points?${query}`,
    );
  }

  exportPlantPreview(input: {
    sessionId: string;
    executionId: string;
    offset: [number, number, number];
    selectedAssetIds?: string[] | null;
  }): Promise<PlantRunExecutionInfo> {
    return this.request(
      `/api/v1/projects/${encodeURIComponent(input.sessionId)}/plant-previews/${encodeURIComponent(input.executionId)}/export`,
      {
        method: "POST",
        body: JSON.stringify({ offset: input.offset, selected_asset_ids: input.selectedAssetIds ?? null }),
      },
    );
  }

  plantPreviewExport(
    sessionId: string,
    executionId: string,
  ): Promise<ExportDownloadInfo> {
    return this.request(
      `/api/v1/projects/${encodeURIComponent(sessionId)}/plant-previews/${encodeURIComponent(executionId)}/export`,
    );
  }

  reexportPlantPreview(input: {
    sessionId: string;
    executionId: string;
    offset: [number, number, number];
    selectedAssetIds?: string[] | null;
  }): Promise<PlantPreviewReexportInfo> {
    return this.request(
      `/api/v1/projects/${encodeURIComponent(input.sessionId)}/plant-previews/${encodeURIComponent(input.executionId)}/re-export`,
      {
        method: "POST",
        body: JSON.stringify({ offset: input.offset, selected_asset_ids: input.selectedAssetIds ?? null }),
      },
    );
  }

  plantPreviewReexport(input: {
    sessionId: string;
    executionId: string;
    exportJobId: string;
  }): Promise<PlantPreviewReexportInfo> {
    const query = new URLSearchParams({ export_job_id: input.exportJobId });
    return this.request(
      `/api/v1/projects/${encodeURIComponent(input.sessionId)}/plant-previews/${encodeURIComponent(input.executionId)}/re-export?${query}`,
    );
  }

  exportInfo(
    sessionId: string,
    exportId: string,
  ): Promise<ExportDownloadInfo> {
    return this.request(
      `/api/v1/projects/${encodeURIComponent(sessionId)}/exports/${encodeURIComponent(exportId)}`,
    );
  }

  private async request<Value>(
    path: string,
    init?: { method?: "GET" | "POST" | "PUT" | "DELETE"; body?: string },
  ): Promise<Value> {
    const transport = await this.bridge.requestLocalApi({
      path,
      method: init?.method,
      body: init?.body,
    });
    if (!transport.ok) {
      throw new LocalApiError(
        transport.message,
        null,
        "connection_failed",
      );
    }

    const response = transport.value;
    if (response.status < 200 || response.status >= 300) {
      let document: ApiErrorDocument = {};
      try {
        document = JSON.parse(response.body) as ApiErrorDocument;
      } catch {
        // A non-JSON transport failure still maps to one stable client error.
      }
      throw new LocalApiError(
        document.error?.message || `本地服务返回 ${response.status}`,
        response.status,
        document.error?.code || "request_failed",
      );
    }
    try {
      return JSON.parse(response.body) as Value;
    } catch {
      throw new LocalApiError(
        "本地服务返回了无效 JSON",
        response.status,
        "invalid_response",
      );
    }
  }
}
