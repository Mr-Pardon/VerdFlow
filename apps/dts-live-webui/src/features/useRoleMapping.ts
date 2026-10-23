import {
  type AssignmentMap,
  type AssetInfo,
  type ProfileSummary,
  type MappingCandidate,
} from "../types";
import { shallowRef, ref, computed, onBeforeUnmount } from "vue";
import {
  profileDetail,
  searchAssets,
  saveProfile,
  assetThumbnailUrl,
} from "../api";
import {
  balanceAssignments,
  balanceCandidateWeights,
} from "../weightBalancing";
import type { Ref } from "vue";
import {
  cloneAssignments,
  readableError,
  candidateKey,
  defaultCandidate,
  normalizeUniformScale,
  cloneScale,
  fixedScale,
  profileStorageKey,
  catalogKey,
} from "../shared/presentation";
import type {
  StrategyOption,
  CatalogSummary,
  ConfigurationOptions,
} from "../types";

export function useRoleMapping(context: {
  selectedStrategy: Ref<StrategyOption | null>;
  selectedProfile: Ref<ProfileSummary | null>;
  selectedCatalog: Ref<CatalogSummary | null>;
  operationActive: Ref<boolean>;
  loadingProfileParameters: Ref<boolean>;
  errorMessage: Ref<string | null>;
  selectedProfileKey: Ref<string>;
  selectedCatalogKey: Ref<string>;
  defaultCatalogKey: () => string;
  parametersValid: Ref<boolean>;
  strategyParameterValues: Ref<Record<string, number>>;
  options: Ref<ConfigurationOptions>;
  profileRoleEnabled: Ref<Record<string, boolean>>;
  selectedStrategyHash: Ref<string>;
}) {
  const {
    selectedStrategy,
    selectedProfile,
    selectedCatalog,
    operationActive,
    loadingProfileParameters,
    errorMessage,
    selectedProfileKey,
    selectedCatalogKey,
    defaultCatalogKey,
    parametersValid,
    strategyParameterValues,
    options,
    profileRoleEnabled,
    selectedStrategyHash,
  } = context;

  interface MappingDraft {
    catalogKey: string;
    assignments: AssignmentMap;
    sourceProfile?: ProfileSummary | null;
  }

  const selectedAssetCache = new Map<string, AssetInfo>();
  const cacheAssets = (items: AssetInfo[]) => {
    for (const item of items) selectedAssetCache.set(item.asset_id, item);
  };


  const assignments = ref<AssignmentMap>({});

  const activeRoleKey = ref("");



  const mappingOpen = ref(false);
  const mappingError = ref<string | null>(null);
  const profileSaveOpen = ref(false);
  const saveNames = { update: "", create: "" };

  const profileSaveMode = ref<"none" | "update" | "create">("none");

  const profileName = ref("");

  const savingFromMain = ref(false);

  const editingProfile = shallowRef<ProfileSummary | null>(null);
  const draftSourceProfile = shallowRef<ProfileSummary | null>(null);
  const preparingPlanSave = ref(false);

  const scaleModeByCandidate = ref<
    Record<string, "catalog" | "fixed" | "uniform_random">
  >({});

  const savingProfile = ref(false);

  const drafts = new Map<string, MappingDraft>();

  let mappingBackup: {
    profileKey: string;
    catalogKey: string;
    assignments: AssignmentMap;
  } | null = null;

  const missingRoles = computed(() =>
    (selectedStrategy.value?.roles ?? [])
      .filter(
        (role) =>
          role.required &&
          assignments.value[role.role_key]?.enabled !== false &&
          !assignments.value[role.role_key]?.candidates.length,
      )
      .map((role) => role.role_key),
  );

  const mappingReady = computed(
    () =>
      selectedProfile.value !== null ||
      (Object.values(assignments.value).some(
          (item) => item.enabled !== false && item.candidates.length > 0,
        ) &&
        missingRoles.value.length === 0),
  );

  const activeRole = computed(
    () =>
      selectedStrategy.value?.roles.find(
        (role) => role.role_key === activeRoleKey.value,
      ) ?? null,
  );

  const activeCandidates = computed(
    () => assignments.value[activeRoleKey.value]?.candidates ?? [],
  );

  const activeRoleEnabled = computed(
    () => assignments.value[activeRoleKey.value]?.enabled !== false,
  );

  const roleEnabledStates = computed(() =>
    Object.fromEntries(
      (selectedStrategy.value?.roles ?? []).map((role) => [
        role.role_key,
        assignments.value[role.role_key]?.enabled !== false,
      ]),
    ),
  );

  const roleCandidateCounts = computed(() =>
    Object.fromEntries(
      Object.entries(assignments.value).map(([key, value]) => [
        key,
        value.candidates.length,
      ]),
    ),
  );

  const enabledRoleCount = computed(
    () =>
      (selectedStrategy.value?.roles ?? []).filter(
        (role) => assignments.value[role.role_key]?.enabled !== false,
      ).length,
  );

  const toggleRole = (event: Event) => {
    const key = activeRoleKey.value;
    const enabled = (event.target as HTMLInputElement).checked;
    if (!key || (!enabled && enabledRoleCount.value <= 1)) return;
    assignments.value = {
      ...assignments.value,
      [key]: {
        ...assignments.value[key],
        candidates: assignments.value[key]?.candidates ?? [],
        enabled,
      },
    };
  };
  const setRoleEnabled = (enabled: Record<string, boolean>) => {
    const roles = selectedStrategy.value?.roles ?? [];
    if (
      savingProfile.value ||
      !roles.some((role) => enabled[role.role_key] !== false)
    )
      return;
    assignments.value = Object.fromEntries(
      roles.map((role) => [
        role.role_key,
        {
          ...assignments.value[role.role_key],
          candidates: assignments.value[role.role_key]?.candidates ?? [],
          enabled: enabled[role.role_key] !== false,
        },
      ]),
    );
  };

  const openMapping = async () => {
    if (
      !selectedStrategy.value ||
      operationActive.value ||
      loadingProfileParameters.value
    )
      return;
    const profile = selectedProfile.value;
    let currentProfile = profile;
    mappingError.value = null;
    profileSaveOpen.value = false;
    savingFromMain.value = false;
    let initialAssignments = cloneAssignments(assignments.value);
    if (profile) {
      try {
        const detail = await profileDetail(profile.profile_id);
        cacheAssets(detail.assets ?? []);
        currentProfile = detail.summary;
        initialAssignments = balanceAssignments(
          cloneAssignments(detail.assignments),
        );
        for (const role of selectedStrategy.value.roles) {
          initialAssignments[role.role_key] ??= { enabled: false, candidates: [] };
        }
      } catch (error) {
        errorMessage.value = readableError(error);
        return;
      }
    }
    mappingBackup = {
      profileKey: selectedProfileKey.value,
      catalogKey: selectedCatalogKey.value,
      assignments: cloneAssignments(assignments.value),
    };
    editingProfile.value =
      currentProfile ??
      drafts.get(selectedStrategyHash.value)?.sourceProfile ??
      null;
    selectedProfileKey.value = "";
    assignments.value = initialAssignments;
    const target = Object.values(initialAssignments).flatMap((item) => item.candidates)
      .find((item) => item.target_asset_ref)?.target_asset_ref;
    const compatible = options.value.catalogs.filter((catalog) => !target ||
      (catalog.target_type === target.target_type && catalog.provider_id === target.provider_id));
    if (!compatible.some((catalog) => catalogKey(catalog) === selectedCatalogKey.value))
      selectedCatalogKey.value = compatible[0] ? catalogKey(compatible[0]) : target ? "" : defaultCatalogKey();
    activeRoleKey.value ||= selectedStrategy.value.roles[0]?.role_key ?? "";
    profileSaveMode.value = "none";
    profileName.value =
      currentProfile?.display_name ??
      `${selectedStrategy.value.summary.display_name} DTS`;
    scaleModeByCandidate.value = {};
    mappingOpen.value = true;
  };

  const toggleAsset = (asset: AssetInfo) => {
    const role = activeRole.value;
    if (!role || !activeRoleEnabled.value) return;
    selectedAssetCache.set(asset.asset_id, asset);
    const existing = activeCandidates.value;
    const selected = existing.some((item) => item.asset_id === asset.asset_id);
    let candidates: MappingCandidate[];
    if (selected) {
      candidates = balanceCandidateWeights(
        existing.filter((item) => item.asset_id !== asset.asset_id),
      );
      const modes = { ...scaleModeByCandidate.value };
      delete modes[candidateKey(role.role_key, asset.asset_id)];
      scaleModeByCandidate.value = modes;
    } else if (role.assignment_policy === "single") {
      candidates = [candidateFromAsset(asset)];
      scaleModeByCandidate.value = {
        ...scaleModeByCandidate.value,
        [candidateKey(role.role_key, asset.asset_id)]: "catalog",
      };
    } else {
      const defaultWeight = existing.length
        ? existing.reduce((sum, candidate) => sum + candidate.weight, 0) /
          existing.length
        : 1;
      candidates = balanceCandidateWeights([
        ...existing,
        { ...candidateFromAsset(asset), weight: defaultWeight },
      ]);
      scaleModeByCandidate.value = {
        ...scaleModeByCandidate.value,
        [candidateKey(role.role_key, asset.asset_id)]: "catalog",
      };
    }
    assignments.value = {
      ...assignments.value,
      [role.role_key]: { ...assignments.value[role.role_key], candidates },
    };
  };
  const candidateFromAsset = (asset: AssetInfo): MappingCandidate => asset.binding_candidate
    ? { ...asset.binding_candidate, ...defaultCandidate(asset), asset_id: asset.asset_id }
    : ({
    ...defaultCandidate(asset),
    ...(selectedCatalog.value
      ? {
          display_name_snapshot: asset.display_name,
          target_asset_ref: {
            target_type: selectedCatalog.value.target_type,
            provider_id: selectedCatalog.value.provider_id,
            locator: asset.provider_metadata,
          },
          ...(asset.reference_radius_m == null
            ? {}
            : { reference_radius_m: asset.reference_radius_m }),
          ...(asset.reference_height_m == null
            ? {}
            : { reference_height_m: asset.reference_height_m }),
        }
      : {}),
  });

  const updateCandidate = (
    assetId: string,
    field: "weight" | "fixed" | "min" | "max",
    raw: string,
  ) => {
    const displayValue = Number(raw);
    if (!Number.isFinite(displayValue) || displayValue <= 0) return;
    const scaleValue = displayValue / 100;
    const roleKey = activeRoleKey.value;
    let candidates = activeCandidates.value.map((candidate) => {
      if (candidate.asset_id !== assetId) return candidate;
      if (field === "weight") return candidate;
      if (field === "fixed") {
        return {
          ...candidate,
          scale: { mode: "fixed", value: scaleValue },
        } as MappingCandidate;
      }
      const scale = normalizeUniformScale(candidate.scale);
      return {
        ...candidate,
        scale: { ...scale, [field]: scaleValue },
      } as MappingCandidate;
    });
    if (field === "weight") {
      candidates = balanceCandidateWeights(
        activeCandidates.value,
        assetId,
        displayValue / 100,
      );
    }
    assignments.value = {
      ...assignments.value,
      [roleKey]: { ...assignments.value[roleKey], candidates },
    };
  };

  const removeCandidate = (assetId: string) => {
    const roleKey = activeRoleKey.value;
    assignments.value = {
      ...assignments.value,
      [roleKey]: {
        ...assignments.value[roleKey],
        candidates: balanceCandidateWeights(
          activeCandidates.value.filter(
            (candidate) => candidate.asset_id !== assetId,
          ),
        ),
      },
    };
    const modes = { ...scaleModeByCandidate.value };
    delete modes[candidateKey(roleKey, assetId)];
    scaleModeByCandidate.value = modes;
  };

  const candidateScaleMode = (candidate: MappingCandidate) =>
    scaleModeByCandidate.value[
      candidateKey(activeRoleKey.value, candidate.asset_id)
    ] ?? candidate.scale.mode;

  const updateScaleMode = (candidate: MappingCandidate, event: Event) => {
    const mode = (event.target as HTMLSelectElement).value as
      "catalog" | "fixed" | "uniform_random";
    const roleKey = activeRoleKey.value;
    const asset = assetForCandidate(candidate.asset_id);
    scaleModeByCandidate.value = {
      ...scaleModeByCandidate.value,
      [candidateKey(roleKey, candidate.asset_id)]: mode,
    };
    const scale =
      mode === "catalog"
        ? cloneScale(asset?.suggested_scale ?? null)
        : mode === "fixed"
          ? { mode: "fixed" as const, value: fixedScale(candidate.scale) }
          : normalizeUniformScale(candidate.scale);
    assignments.value = {
      ...assignments.value,
      [roleKey]: {
        ...assignments.value[roleKey],
        candidates: activeCandidates.value.map((item) =>
          item.asset_id === candidate.asset_id ? { ...item, scale } : item,
        ),
      },
    };
  };

  const beginProfileSave = () => {
    if (!mappingReady.value || savingProfile.value) return;
    mappingError.value = null;
    saveNames.update = editingProfile.value?.display_name ?? "";
    saveNames.create = editingProfile.value
      ? `${editingProfile.value.display_name.slice(0, 77)} 副本`
      : profileName.value;
    profileSaveMode.value = editingProfile.value ? "update" : "create";
    profileName.value = saveNames[profileSaveMode.value];
    profileSaveOpen.value = true;
  };
  const beginPlanSave = async () => {
    if (
      !mappingReady.value ||
      !parametersValid.value ||
      operationActive.value ||
      loadingProfileParameters.value ||
      savingProfile.value ||
      preparingPlanSave.value
    )
      return;
    const strategyHash = selectedStrategyHash.value;
    const profileKey = selectedProfileKey.value;
    preparingPlanSave.value = true;
    mappingError.value = null;
    const profile = selectedProfile.value;
    let initial = cloneAssignments(assignments.value);
    let source = drafts.get(selectedStrategyHash.value)?.sourceProfile ?? null;
    if (profile) {
      try {
        const detail = await profileDetail(profile.profile_id);
        initial = cloneAssignments(detail.assignments);
        cacheAssets(detail.assets ?? []);
        source = detail.summary;
      } catch (error) {
        errorMessage.value = readableError(error);
        preparingPlanSave.value = false;
        return;
      }
    }
    preparingPlanSave.value = false;
    if (
      strategyHash !== selectedStrategyHash.value ||
      profileKey !== selectedProfileKey.value ||
      operationActive.value
    )
      return;
    mappingBackup = {
      profileKey: selectedProfileKey.value,
      catalogKey: selectedCatalogKey.value,
      assignments: cloneAssignments(assignments.value),
    };
    assignments.value = initial;
    editingProfile.value = source;
    selectedProfileKey.value = "";
    profileName.value =
      source?.display_name ??
      `${selectedStrategy.value?.summary.display_name ?? "种植"}方案`;
    savingFromMain.value = true;
    beginProfileSave();
  };

  const chooseProfileSaveMode = (mode: "update" | "create") => {
    if (savingProfile.value || (mode === "update" && !editingProfile.value))
      return;
    if (profileSaveMode.value !== "none")
      saveNames[profileSaveMode.value] = profileName.value;
    profileSaveMode.value = mode;
    profileName.value = saveNames[mode];
    mappingError.value = null;
  };

  const cancelProfileSave = () => {
    if (savingProfile.value) return;
    if (savingFromMain.value) {
      cancelMapping();
      return;
    }
    profileSaveOpen.value = false;
    mappingError.value = null;
  };

  const finishMapping = async (mode = profileSaveMode.value) => {
    if (savingProfile.value) return;
    mappingError.value = null;
    if (
      !mappingReady.value ||
      !selectedStrategy.value
    ) {
      mappingError.value = "请为所有必需用途选择植被";
      return;
    }
    if (mode !== "none" && !profileName.value.trim()) {
      mappingError.value = "请输入植被搭配名称";
      return;
    }
    if (mode === "update" && !editingProfile.value) {
      mappingError.value = "当前没有可更新的植被搭配";
      return;
    }
    if (mode !== "none") {
      if (!parametersValid.value) {
        mappingError.value = "请先检查种植参数";
        return;
      }
      savingProfile.value = true;
      try {
        const updating = mode === "update" ? editingProfile.value : null;
        const detail = await saveProfile({
          ...(updating
            ? {
                profile_id: updating.profile_id,
                expected_generation: updating.generation,
              }
            : {}),
          ...(!updating && editingProfile.value
            ? { source_profile_id: editingProfile.value.profile_id }
            : {}),
          display_name: profileName.value.trim(),
          strategy_id: selectedStrategy.value.summary.strategy_id,
          strategy_version: selectedStrategy.value.summary.strategy_version,
          catalog_id: selfContained() ? null : selectedCatalog.value?.catalog_id ?? null,
          assignments: cloneAssignments(assignments.value),
          strategy_parameters: { ...strategyParameterValues.value },
        });
        // Apply the successful response locally; a later sync failure must not invite a duplicate save.
        options.value = {
          ...options.value,
          strategies: options.value.strategies.map((strategy) =>
            strategy.summary.package_hash === selectedStrategyHash.value
              ? {
                  ...strategy,
                  profiles: [
                    ...strategy.profiles.filter(
                      (profile) =>
                        profile.profile_id !== detail.summary.profile_id,
                    ),
                    detail.summary,
                  ],
                }
              : strategy,
          ),
        };
        selectedProfileKey.value = detail.summary.profile_id;
        profileRoleEnabled.value = Object.fromEntries(
          Object.entries(detail.assignments).map(([key, assignment]) => [
            key,
            assignment.enabled !== false,
          ]),
        );
        localStorage.setItem(
          profileStorageKey(selectedStrategy.value),
          detail.summary.profile_id,
        );
        assignments.value = {};
      } catch (error) {
        mappingError.value = readableError(error);
        return;
      } finally {
        savingProfile.value = false;
      }
    } else {
      draftSourceProfile.value = editingProfile.value;
      persistDraft();
    }
    mappingBackup = null;
    editingProfile.value = null;
    profileSaveOpen.value = false;
    mappingOpen.value = false;
    savingFromMain.value = false;
    errorMessage.value = null;
  };

  const cancelMapping = () => {
    if (savingProfile.value) return;
    profileSaveOpen.value = false;
    mappingError.value = null;
    if (mappingBackup) {
      selectedProfileKey.value = mappingBackup.profileKey;
      selectedCatalogKey.value = mappingBackup.catalogKey;
      assignments.value = cloneAssignments(mappingBackup.assignments);
    }
    mappingBackup = null;
    editingProfile.value = null;
    mappingOpen.value = false;
    savingFromMain.value = false;
  };

  const persistDraft = () => {
    if (!selectedStrategyHash.value || selectedProfileKey.value) return;
    drafts.set(selectedStrategyHash.value, {
      catalogKey: selectedCatalogKey.value,
      assignments: cloneAssignments(assignments.value),
      sourceProfile:
        editingProfile.value ??
        drafts.get(selectedStrategyHash.value)?.sourceProfile,
    });
  };

  const assetForCandidate = (assetId: string) =>
    selectedAssetCache.get(assetId) ?? null;

  const selfContained = () => Object.values(assignments.value).every((assignment) =>
    assignment.candidates.every((candidate) => Boolean(candidate.target_asset_ref)));

  const thumbnailFor = (asset: AssetInfo | null) =>
    asset?.thumbnail_resource_id
      ? assetThumbnailUrl(asset.thumbnail_resource_id)
      : null;

  onBeforeUnmount(() => {
    selectedAssetCache.clear();
  });

  return {
    searchAssets,
    assignments,
    activeRoleKey,
    mappingOpen,
    mappingError,
    profileSaveOpen,
    profileSaveMode,
    profileName,
    editingProfile,
    draftSourceProfile,
    preparingPlanSave,
    scaleModeByCandidate,
    savingProfile,
    drafts,
    missingRoles,
    mappingReady,
    activeRole,
    activeCandidates,
    activeRoleEnabled,
    roleEnabledStates,
    roleCandidateCounts,
    enabledRoleCount,
    toggleRole,
    setRoleEnabled,
    openMapping,
    toggleAsset,
    updateCandidate,
    removeCandidate,
    candidateScaleMode,
    updateScaleMode,
    finishMapping,
    applyMapping: () => finishMapping("none"),
    beginProfileSave,
    beginPlanSave,
    chooseProfileSaveMode,
    cancelProfileSave,
    cancelMapping,
    persistDraft,
    assetForCandidate,
    thumbnailFor,
    resourceThumbnail: (resourceId: string | null) =>
      resourceId ? assetThumbnailUrl(resourceId) : null,
  };
}
