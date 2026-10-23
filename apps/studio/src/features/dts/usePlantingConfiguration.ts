import { computed, ref, shallowRef, watch } from "vue";

import { LocalApiClient, LocalApiError } from "../../api";
import type {
  AssetCatalogAssetInfo,
  AssetCatalogCollectionInfo,
  AssetCatalogSummary,
  PlantRunExecutionInfo,
  PlantPreviewInputInfo,
  RoleBindingProfileDetail,
  RoleBindingProfileSummary,
  StrategyLibraryDetail,
  StrategyLibrarySummary,
} from "../../api";
import { platformBridge } from "../../platform";
import { parameterPresetValues, visibleParameters } from "../shared/parameterPresets";
import {
  canonicalAssignments,
  bindingBrowserAssets,
  defaultCandidate,
  parseAssignments,
  parseRoleEnabled,
  serializeMappings,
  validCandidates,
} from "../shared/roleMapping";
import type {
  MappingCandidateDraft,
  RoleMappingDraft,
} from "../shared/roleMapping";

const TARGET_TYPE = "dts";
const ACTIVE_PREVIEW_STATUSES = new Set(["queued", "running", "cancel_requested"]);

export type { MappingCandidateDraft, MappingScaleMode } from "../shared/roleMapping";

export const usePlantingConfiguration = (input: {
  sessionId: string;
  sceneRevisionId: string;
  regionSetId: string;
  selectedRegionIds: () => string[];
  initialInput?: PlantPreviewInputInfo | null;
  currentExecution: () => PlantRunExecutionInfo | null;
  onPreviewSubmitted: (
    execution: PlantRunExecutionInfo,
    previewInput: PlantPreviewInputInfo | null,
  ) => void;
}) => {
  const bridge = platformBridge();
  const client = new LocalApiClient(bridge);
  const strategies = shallowRef<StrategyLibrarySummary[]>([]);
  const catalogs = shallowRef<AssetCatalogSummary[]>([]);
  const assets = shallowRef<AssetCatalogAssetInfo[]>([]);
  const collections = shallowRef<AssetCatalogCollectionInfo[]>([]);
  const profiles = shallowRef<RoleBindingProfileSummary[]>([]);
  const strategyDetail = shallowRef<StrategyLibraryDetail | null>(null);
  const profileDetail = shallowRef<RoleBindingProfileDetail | null>(null);
  const roleMappings = shallowRef<RoleMappingDraft>({});
  const roleEnabled = ref<Record<string, boolean>>({});
  const selectedStrategyHash = ref("");
  const selectedCatalogKey = ref("");
  const selectedProfileId = ref("");
  const execution = shallowRef<PlantRunExecutionInfo | null>(
    input.currentExecution(),
  );
  const randomSeed = ref<number | null>(null);
  const strategyParameterValues = ref<Record<string, number>>({});
  const submittedParameterSignature = ref<string | null>(null);
  const loading = ref(true);
  const loadingDetails = ref(false);
  const submitting = ref(false);
  const savingProfile = ref(false);
  const syncingProfileAssets = ref(false);
  const trustingStrategyHash = ref("");
  const managementAvailable = ref(false);
  const errorMessage = ref<string | null>(null);
  const profileAssetSyncMessage = ref<string | null>(null);
  const mappingBaseline = ref(canonicalAssignments({}));
  let submissionKey: string | null = null;

  watch(input.currentExecution, (current) => {
    execution.value = current;
    if (current && ["failed", "cancelled"].includes(current.status)) submissionKey = null;
  });

  const generating = computed(
    () => submitting.value || ACTIVE_PREVIEW_STATUSES.has(execution.value?.status ?? ""),
  );

  const selectedStrategy = computed(
    () => strategies.value.find((item) => item.package_hash === selectedStrategyHash.value) ?? null,
  );
  const selectedCatalog = computed(
    () => catalogs.value.find((item) => catalogKey(item) === selectedCatalogKey.value) ?? null,
  );
  const selectedProfile = computed(
    () =>
      profiles.value.find((item) => profileKey(item) === selectedProfileId.value) ??
      (profileDetail.value && profileKey(profileDetail.value.summary) === selectedProfileId.value
        ? profileDetail.value.summary
        : null),
  );
  const selectedMappingKey = computed(() => selectedProfile.value?.profile_id ?? "");
  const currentAssignments = computed(() => serializeMappings(roleMappings.value, roleEnabled.value));
  const missingRequiredRoles = computed(() =>
    (strategyDetail.value?.roles ?? [])
      .filter((role) => roleEnabled.value[role.role_key] !== false && role.required && !validCandidates(roleMappings.value[role.role_key]))
      .map((role) => role.role_key),
  );
  const mappingReady = computed(
    () =>
      strategyDetail.value !== null &&
      missingRequiredRoles.value.length === 0 &&
      Object.values(currentAssignments.value).some((item) => item.enabled !== false && Array.isArray(item.candidates) && item.candidates.length > 0),
  );
  const mappingDirty = computed(
    () => canonicalAssignments(currentAssignments.value) !== mappingBaseline.value,
  );
  const syncableProfileAssetCount = computed(() =>
    Object.values(profileDetail.value?.catalog_drift ?? {}).filter(
      (status) => status === "metadata_changed",
    ).length,
  );
  const mappingSourceLabel = computed(() => {
    if (!selectedProfile.value) return "本次自定义搭配";
    return mappingDirty.value ? "已临时调整搭配" : "已选择植被搭配";
  });
  const generationParameters = computed(
    () => strategyDetail.value?.generation_parameters ?? [],
  );
  const visibleGenerationParameters = computed(() => visibleParameters(generationParameters.value, roleEnabled.value));
  const customizedParameterCount = computed(() =>
    visibleGenerationParameters.value.filter(
      (parameter) =>
        strategyParameterValues.value[parameter.key] !== parameter.default,
    ).length,
  );
  const parameterSummary = computed(() => {
    const parameters = visibleGenerationParameters.value;
    if (!parameters.length) return "使用策略内置设置";
    return parameters
      .filter((parameter) => parameter.group === "quick")
      .slice(0, 3)
      .map(
        (parameter) =>
          `${parameter.display_name} ${strategyParameterValues.value[parameter.key] ?? parameter.default}`,
      )
      .join(" · ");
  });
  const parametersValid = computed(() =>
    generationParameters.value.every((parameter) => {
      const value = strategyParameterValues.value[parameter.key];
      return (
        Number.isFinite(value) &&
        value >= parameter.minimum &&
        value <= parameter.maximum
      );
    }),
  );
  const currentParameterSignature = computed(() => parameterSignature(
    selectedStrategyHash.value,
    randomSeed.value,
    generationParameters.value.map((parameter) => [
      parameter.key,
      strategyParameterValues.value[parameter.key] ?? parameter.default,
    ]),
  ));
  const parametersChangedSinceGeneration = computed(
    () =>
      submittedParameterSignature.value !== null &&
      submittedParameterSignature.value !== currentParameterSignature.value,
  );
  const canSaveProfile = computed(
    () =>
      managementAvailable.value &&
      selectedStrategy.value !== null &&
      mappingReady.value &&
      !savingProfile.value,
  );
  const canSubmit = computed(
    () =>
      input.selectedRegionIds().length > 0 &&
      selectedStrategy.value !== null &&
      selectedStrategy.value.executable &&
      mappingReady.value &&
      parametersValid.value &&
      (randomSeed.value === null || Number.isSafeInteger(randomSeed.value)) &&
      !loadingDetails.value &&
      !loading.value &&
      !generating.value,
  );

  const clearMappings = () => {
    roleEnabled.value = {};
    selectedProfileId.value = "";
    profileDetail.value = null;
    profileAssetSyncMessage.value = null;
    roleMappings.value = {};
    mappingBaseline.value = canonicalAssignments({});
  };

  const loadProfiles = async () => {
    const strategy = selectedStrategy.value;
    if (!strategy) {
      profiles.value = [];
      return;
    }
    const page = await client.roleBindingProfiles({
      targetType: TARGET_TYPE,
      strategyId: strategy.strategy_id,
      strategyVersion: strategy.strategy_version,
    });
    profiles.value = page.items;
  };

  const chooseStrategy = async (packageHash: string) => {
    selectedStrategyHash.value = packageHash;
    strategyDetail.value = null;
    profiles.value = [];
    clearMappings();
    submissionKey = null;
    if (!packageHash) return;
    loadingDetails.value = true;
    errorMessage.value = null;
    try {
      strategyDetail.value = await client.strategy(packageHash);
      resetStrategyParameters();
      await loadProfiles();
    } catch (error) {
      errorMessage.value = readableError(error);
    } finally {
      loadingDetails.value = false;
    }
  };

  const trustStrategy = async (packageHash: string) => {
    const strategy = strategies.value.find(
      (item) => item.package_hash === packageHash,
    );
    if (!strategy) return false;
    if (strategy.executable) return true;
    if (!managementAvailable.value) {
      errorMessage.value = "当前桌面会话没有策略信任管理权限";
      return false;
    }
    trustingStrategyHash.value = packageHash;
    errorMessage.value = null;
    try {
      const trusted = await client.trustStrategy(packageHash);
      strategies.value = strategies.value.map((item) =>
        item.package_hash === packageHash ? trusted : item,
      );
      return trusted.executable;
    } catch (error) {
      errorMessage.value = readableError(error);
      return false;
    } finally {
      trustingStrategyHash.value = "";
    }
  };

  const chooseCatalog = async (key: string) => {
    const catalogChoice = catalogs.value.find((item) => catalogKey(item) === key);
    const target = Object.values(roleMappings.value).flat().find((item) => item.bindingCandidate?.target_asset_ref)
      ?.bindingCandidate?.target_asset_ref as { target_type: string; provider_id: string } | undefined;
    if (catalogChoice && target && (target.target_type !== catalogChoice.target_type || target.provider_id !== catalogChoice.provider_id)) {
      errorMessage.value = "此资产库与已选植被的目标类型或 Provider 不兼容";
      return;
    }
    selectedCatalogKey.value = key;
  };

  const chooseProfile = async (key: string, applyPreset = true) => {
    selectedProfileId.value = key;
    profileDetail.value = null;
    profileAssetSyncMessage.value = null;
    roleMappings.value = {};
    roleEnabled.value = {};
    mappingBaseline.value = canonicalAssignments({});
    submissionKey = null;
    if (!key) return;
    loadingDetails.value = true;
    errorMessage.value = null;
    try {
      const detail = await client.roleBindingProfile(key);
      profileDetail.value = detail;
      roleMappings.value = parseAssignments(detail.assignments, detail.assets ?? []);
      if (applyPreset) {
        strategyParameterValues.value = parameterPresetValues(generationParameters.value, detail.strategy_parameters ?? {});
      }
      const sourceIds = new Set((detail.assets ?? []).flatMap((asset) => {
        const link = asset.binding_candidate?.catalog_link as { catalog_id: string } | undefined;
        return link ? [link.catalog_id] : [];
      }));
      const linkedCatalog = catalogs.value.find((item) => item.catalog_id === detail.summary.catalog_id)
        ?? catalogs.value.find((item) => sourceIds.has(item.catalog_id) && item.provider_id === detail.summary.provider_id)
        ?? null;
      if (linkedCatalog) {
        await chooseCatalog(catalogKey(linkedCatalog));
      } else if (detail.summary.catalog_id || (selectedCatalog.value && selectedCatalog.value.provider_id !== detail.summary.provider_id)) {
        selectedCatalogKey.value = "";
        assets.value = [];
        collections.value = [];
      }
      roleMappings.value = parseAssignments(detail.assignments, [...(detail.assets ?? []), ...assets.value]);
      assets.value = bindingBrowserAssets(assets.value, roleMappings.value);
      roleEnabled.value = parseRoleEnabled(detail.assignments, strategyDetail.value?.roles ?? []);
      mappingBaseline.value = canonicalAssignments(serializeMappings(roleMappings.value, roleEnabled.value));
    } catch (error) {
      errorMessage.value = readableError(error);
    } finally {
      loadingDetails.value = false;
    }
  };

  const chooseMappingSource = async (key: string) => {
    if (!key) {
      clearMappings();
      return;
    }
    await chooseProfile(key);
  };

  const initialize = async () => {
    loading.value = true;
    errorMessage.value = null;
    try {
      const [strategyPage, catalogPage, connection] = await Promise.all([
        client.strategies(),
        client.assetCatalogs(TARGET_TYPE),
        bridge.getLocalApiConnection(),
      ]);
      managementAvailable.value = connection.ok && connection.value.managementAvailable;
      strategies.value = strategyPage.items;
      catalogs.value = catalogPage.items;
      const initial = input.initialInput;
      if (!initial) return;
      await chooseStrategy(initial.strategy_package_hash);
      randomSeed.value = initial.random_seed;
      restoreStrategyParameters(initial.generation_args);
      submittedParameterSignature.value = currentParameterSignature.value;
      if (initial.catalog_id) {
        await chooseCatalog(initial.catalog_id);
      }
      if (initial.profile_id) await chooseProfile(initial.profile_id, false);
      if (Object.keys(initial.binding_overrides ?? {}).length > 0) {
        roleMappings.value = {
          ...roleMappings.value,
          ...parseAssignments(initial.binding_overrides, assets.value),
        };
        roleEnabled.value = { ...roleEnabled.value, ...parseRoleEnabled(initial.binding_overrides) };
      }
    } catch (error) {
      errorMessage.value = readableError(error);
    } finally {
      loading.value = false;
    }
  };

  const setRoleAssets = (roleKey: string, assetIds: string[]) => {
    const existing = new Map(
      (roleMappings.value[roleKey] ?? []).map((item) => [item.assetId, item]),
    );
    roleMappings.value = {
      ...roleMappings.value,
      [roleKey]: assetIds.map(
        (assetId) =>
          existing.get(assetId) ??
          defaultCandidate(assetId, assets.value.find((asset) => asset.asset_id === assetId)),
      ),
    };
    submissionKey = null;
  };

  const updateCandidate = (
    roleKey: string,
    assetId: string,
    patch: Partial<MappingCandidateDraft>,
  ) => {
    roleMappings.value = {
      ...roleMappings.value,
      [roleKey]: (roleMappings.value[roleKey] ?? []).map((candidate) =>
        candidate.assetId === assetId ? { ...candidate, ...patch } : candidate,
      ),
    };
    submissionKey = null;
  };

  const removeCandidate = (roleKey: string, assetId: string) => {
    setRoleAssets(
      roleKey,
      (roleMappings.value[roleKey] ?? [])
        .filter((candidate) => candidate.assetId !== assetId)
        .map((candidate) => candidate.assetId),
    );
  };

  const replaceRoleMappings = (mappings: RoleMappingDraft) => {
    roleMappings.value = mappings;
    submissionKey = null;
  };

  const updateStrategyParameter = (key: string, value: number) => {
    const parameter = generationParameters.value.find((item) => item.key === key);
    if (!parameter || !Number.isFinite(value)) return;
    strategyParameterValues.value = {
      ...strategyParameterValues.value,
      [key]: Math.min(parameter.maximum, Math.max(parameter.minimum, value)),
    };
    submissionKey = null;
  };

  const updateRandomSeed = (value: number) => {
    if (!Number.isSafeInteger(value)) return;
    randomSeed.value = value;
    submissionKey = null;
  };

  const rerollRandomSeed = () => {
    const value = new Uint32Array(1);
    crypto.getRandomValues(value);
    updateRandomSeed(value[0]);
  };

  function resetStrategyParameters() {
    const detail = strategyDetail.value;
    strategyParameterValues.value = Object.fromEntries(
      (detail?.generation_parameters ?? []).map((parameter) => [
        parameter.key,
        parameter.default,
      ]),
    );
    randomSeed.value = detail?.default_random_seed ?? null;
    submissionKey = null;
  }

  function restoreStrategyParameters(generationArgs: Record<string, unknown>) {
    const raw = generationArgs.strategy_parameters;
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return;
    const stored = raw as Record<string, unknown>;
    strategyParameterValues.value = Object.fromEntries(
      generationParameters.value.map((parameter) => {
        const value = stored[parameter.key];
        return [
          parameter.key,
          typeof value === "number" && Number.isFinite(value)
            ? value
            : parameter.default,
        ];
      }),
    );
  }

  const saveProfile = async (displayName: string, saveAsNew: boolean) => {
    const strategy = selectedStrategy.value;
    const catalog = selectedCatalog.value;
    if (!strategy || !canSaveProfile.value || !displayName.trim()) return null;
    if (!parametersValid.value) return null;
    savingProfile.value = true;
    errorMessage.value = null;
    try {
      const base = saveAsNew ? null : selectedProfile.value;
      const detail = await client.saveRoleBindingProfile({
        profileId: base?.profile_id ?? null,
        sourceProfileId: saveAsNew ? selectedProfile.value?.profile_id : undefined,
        displayName: displayName.trim(),
        strategyId: strategy.strategy_id,
        strategyVersion: strategy.strategy_version,
        targetType: TARGET_TYPE,
        catalogId: selfContainedAssignments(currentAssignments.value) ? null : catalog?.catalog_id ?? null,
        assignments: currentAssignments.value,
        strategyParameters: { ...strategyParameterValues.value },
        expectedGeneration: base?.generation ?? 0,
      });
      await loadProfiles();
      profileDetail.value = detail;
      selectedProfileId.value = detail.summary.profile_id;
      mappingBaseline.value = canonicalAssignments(serializeMappings(roleMappings.value, roleEnabled.value));
      submissionKey = null;
      return detail;
    } catch (error) {
      errorMessage.value = readableError(error);
      return null;
    } finally {
      savingProfile.value = false;
    }
  };

  const syncSelectedProfileAssets = async () => {
    const detail = profileDetail.value;
    const syncCount = syncableProfileAssetCount.value;
    if (!detail || !syncCount || syncingProfileAssets.value) return false;
    if (mappingDirty.value) {
      errorMessage.value = "请先保存或放弃本次临时映射修改，再同步资产信息";
      return false;
    }
    syncingProfileAssets.value = true;
    errorMessage.value = null;
    profileAssetSyncMessage.value = null;
    try {
      const synced = await client.syncRoleBindingProfileAssets(
        detail.summary.profile_id,
        detail.summary.generation,
      );
      profileDetail.value = synced;
      profiles.value = profiles.value.map((profile) =>
        profile.profile_id === synced.summary.profile_id
          ? synced.summary
          : profile,
      );
      roleMappings.value = parseAssignments(synced.assignments, [...(synced.assets ?? []), ...assets.value]);
      roleEnabled.value = parseRoleEnabled(synced.assignments, strategyDetail.value?.roles ?? []);
      mappingBaseline.value = canonicalAssignments(
        serializeMappings(roleMappings.value, roleEnabled.value),
      );
      submissionKey = null;
      profileAssetSyncMessage.value = `已同步 ${syncCount} 项资产信息，重新生成后将写入新的预览批次`;
      return true;
    } catch (error) {
      errorMessage.value = readableError(error);
      return false;
    } finally {
      syncingProfileAssets.value = false;
    }
  };

  const submitPreview = async () => {
    const strategy = selectedStrategy.value;
    const catalog = selectedCatalog.value;
    if (!strategy || !canSubmit.value) return;
    const useProfile = selectedProfile.value !== null;
    submitting.value = true;
    errorMessage.value = null;
    submissionKey ??= crypto.randomUUID();
    try {
      const request = {
        sessionId: input.sessionId,
        sceneRevisionId: input.sceneRevisionId,
        regionSetId: input.regionSetId,
        selectedRegionIds: [...input.selectedRegionIds()],
        strategyPackageHash: strategy.package_hash,
        randomSeed: randomSeed.value,
        generationArgs: generationParameters.value.length
          ? { strategy_parameters: { ...strategyParameterValues.value } }
          : {},
        catalogId: useProfile || selfContainedAssignments(currentAssignments.value) ? null : catalog?.catalog_id ?? null,
        profileId: useProfile ? selectedProfile.value?.profile_id : null,
        bindingOverrides: useProfile
          ? (mappingDirty.value
              ? executableProfileOverrides(
                  currentAssignments.value,
                  profileDetail.value,
                  assets.value,
                  catalog,
                )
              : {})
          : currentAssignments.value,
        idempotencyKey: submissionKey,
      };
      const result = await client.submitPlantPreview(request);
      execution.value = result.execution;
      randomSeed.value = result.random_seed;
      submittedParameterSignature.value = currentParameterSignature.value;
      if (["failed", "cancelled"].includes(result.execution.status)) submissionKey = null;
      input.onPreviewSubmitted(result.execution, {
        scene_revision_id: request.sceneRevisionId,
        region_set_id: request.regionSetId,
        selected_region_ids: request.selectedRegionIds,
        strategy_package_hash: request.strategyPackageHash,
        target_type: TARGET_TYPE,
        random_seed: result.random_seed,
        generation_args: request.generationArgs,
        catalog_id: request.catalogId,
        profile_id: request.profileId ?? null,
        binding_overrides: request.bindingOverrides,
      });
    } catch (error) {
      errorMessage.value = readableError(error);
    } finally {
      submitting.value = false;
    }
  };

  const retryAssignment = async () => {
    const current = execution.value;
    if (
      !current ||
      current.status !== "failed" ||
      current.stage !== "assignment" ||
      !current.generated_point_set_id
    ) return;
    submitting.value = true;
    errorMessage.value = null;
    try {
      execution.value = await client.retryPlantPreviewAssignment(
        input.sessionId,
        current.execution_id,
      );
      input.onPreviewSubmitted(execution.value, null);
    } catch (error) {
      errorMessage.value = readableError(error);
    } finally {
      submitting.value = false;
    }
  };

  return {
    assets,
    canSaveProfile,
    canSubmit,
    catalogs,
    collections,
    chooseCatalog,
    chooseMappingSource,
    chooseProfile,
    chooseStrategy,
    currentAssignments,
    errorMessage,
    execution,
    customizedParameterCount,
    generationParameters,
    visibleGenerationParameters,
    generating,
    initialize,
    loading,
    loadingDetails,
    managementAvailable,
    mappingDirty,
    mappingReady,
    mappingSourceLabel,
    missingRequiredRoles,
    profileDetail,
    profileAssetSyncMessage,
    profiles,
    randomSeed,
    parameterSummary,
    parametersChangedSinceGeneration,
    parametersValid,
    replaceRoleMappings,
    retryAssignment,
    removeCandidate,
    roleMappings,
    roleEnabled,
    setRoleEnabled: (value: Record<string, boolean>) => { roleEnabled.value = value; submissionKey = null; },
    saveProfile,
    savingProfile,
    selectedCatalog,
    selectedCatalogKey,
    selectedProfile,
    selectedProfileId,
    selectedMappingKey,
    selectedStrategy,
    selectedStrategyHash,
    setRoleAssets,
    strategies,
    strategyDetail,
    strategyParameterValues,
    submitPreview,
    submitting,
    trustStrategy,
    trustingStrategyHash,
    syncableProfileAssetCount,
    syncingProfileAssets,
    syncSelectedProfileAssets,
    updateCandidate,
    updateRandomSeed,
    updateStrategyParameter,
    rerollRandomSeed,
    resetStrategyParameters,
  };
};

export const catalogKey = (catalog: AssetCatalogSummary) =>
  catalog.catalog_id;

export const profileKey = (profile: RoleBindingProfileSummary) =>
  profile.profile_id;

const executableProfileOverrides = (
  assignments: Record<string, Record<string, unknown>>,
  detail: RoleBindingProfileDetail | null,
  assets: AssetCatalogAssetInfo[],
  catalog: AssetCatalogSummary | null,
) => Object.fromEntries(
  Object.entries(assignments).map(([roleKey, assignment]) => {
    const base = detail?.assignments[roleKey];
    const baseCandidates = new Map(
      Array.isArray(base?.candidates)
        ? base.candidates.flatMap((candidate) => {
            if (!candidate || typeof candidate !== "object") return [];
            const item = candidate as Record<string, unknown>;
            return typeof item.asset_id === "string" ? [[item.asset_id, item] as const] : [];
          })
        : [],
    );
    const candidates = Array.isArray(assignment.candidates)
      ? assignment.candidates.map((candidate) => {
          const value = candidate as Record<string, unknown>;
          if (value.target_asset_ref) return value;
          const assetId = String(value.asset_id ?? "");
          const existing = baseCandidates.get(assetId);
          if (existing) {
            return {
              ...existing,
              weight: value.weight,
              scale: value.scale ?? existing.scale,
            };
          }
          const asset = assets.find((item) => item.asset_id === assetId);
          if (!asset || !catalog) {
            throw new Error(`资产 ${assetId} 需要从资产库重新选择后才能加入本次映射`);
          }
          return {
            asset_id: assetId,
            display_name_snapshot: asset.display_name,
            target_asset_ref: {
              target_type: catalog.target_type,
              provider_id: catalog.provider_id,
              locator: asset.provider_metadata,
            },
            weight: value.weight,
            scale: value.scale ?? asset.suggested_scale ?? { mode: "fixed", value: 1 },
            ...(asset.reference_radius_m === null
              ? {}
              : { reference_radius_m: asset.reference_radius_m }),
            ...(asset.reference_height_m == null
              ? {}
              : { reference_height_m: asset.reference_height_m }),
          };
        })
      : [];
    return [roleKey, { enabled: assignment.enabled !== false, candidates }];
  }),
);

const selfContainedAssignments = (assignments: Record<string, Record<string, unknown>>) =>
  Object.values(assignments).every((assignment) => Array.isArray(assignment.candidates) &&
    assignment.candidates.every((candidate) => Boolean((candidate as Record<string, unknown>).target_asset_ref)));

const readableError = (error: unknown): string => {
  if (error instanceof LocalApiError) {
    return error.code === "connection_failed" ? "无法连接 VerdFlow 本地服务" : error.message;
  }
  return error instanceof Error ? error.message : "种植配置读取失败";
};

const parameterSignature = (
  strategyHash: string,
  randomSeed: number | null,
  parameters: Array<[string, number]>,
) => JSON.stringify({ strategyHash, randomSeed, parameters });
