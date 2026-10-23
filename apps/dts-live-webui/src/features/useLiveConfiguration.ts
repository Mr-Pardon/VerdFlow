import { shallowRef, ref, computed, onMounted, onBeforeUnmount } from "vue";
import {
  type ConfigurationOptions,
  type StrategyOption,
  type SelectionConfiguration,
  type ProfileSummary,
  type ProfileDetail,
} from "../types";
import { profileDetail, configurationOptions } from "../api";
import type { Ref } from "vue";
import { useRoleMapping } from "./useRoleMapping";
import { useStrategyParameters } from "./useStrategyParameters";
import {
  compareVersions,
  profileKey,
  catalogKey,
  profileStorageKey,
  cloneAssignments,
  readableError,
} from "../shared/presentation";

export function useLiveConfiguration(context: {
  operationActive: Ref<boolean>;
  loading: Ref<boolean>;
  errorMessage: Ref<string | null>;
}) {
  const { operationActive, loading, errorMessage } = context;
  const options = shallowRef<ConfigurationOptions>({
    strategies: [],
    catalogs: [],
  });

  const selectedStrategyHash = ref("");

  const selectedProfileKey = ref("");

  const selectedCatalogKey = ref("");

  const profileRoleEnabled = ref<Record<string, boolean>>({});
  const selectedProfileDetail = shallowRef<ProfileDetail | null>(null);

  const loadingProfileParameters = ref(false);

  let profileParameterEpoch = 0;

  const refreshingConfiguration = ref(false);

  const selectedStrategy = computed(
    () =>
      options.value.strategies.find(
        (item) => item.summary.package_hash === selectedStrategyHash.value,
      ) ?? null,
  );

  const strategyFamilies = computed(() => {
    const byId = new Map<string, StrategyOption[]>();
    for (const strategy of options.value.strategies) {
      const versions = byId.get(strategy.summary.strategy_id) ?? [];
      versions.push(strategy);
      byId.set(strategy.summary.strategy_id, versions);
    }
    return [...byId.entries()].map(([strategyId, versions]) => {
      versions.sort((left, right) =>
        compareVersions(
          right.summary.strategy_version,
          left.summary.strategy_version,
        ),
      );
      return {
        strategyId,
        versions,
        selected:
          versions.find(
            (strategy) =>
              strategy.summary.package_hash === selectedStrategyHash.value,
          ) ?? versions[0],
      };
    });
  });

  const selectedStrategyVersions = computed(
    () =>
      strategyFamilies.value.find(
        (family) =>
          family.strategyId === selectedStrategy.value?.summary.strategy_id,
      )?.versions ?? [],
  );

  const selectedProfile = computed(
    () =>
      selectedStrategy.value?.profiles.find(
        (profile) => profileKey(profile) === selectedProfileKey.value,
      ) ?? null,
  );

  const selectedCatalog = computed(
    () =>
      options.value.catalogs.find(
        (catalog) => catalogKey(catalog) === selectedCatalogKey.value,
      ) ?? null,
  );

  const parameters = useStrategyParameters({ selectedStrategy });
  const {
    strategyParameterValues,
    randomSeed,
    parametersValid,
    persistParameterDraft,
    restoreParameterDraft,
  } = parameters;
  const mapping = useRoleMapping({
    operationActive,
    errorMessage,
    selectedStrategy,
    selectedStrategyHash,
    selectedProfile,
    selectedProfileKey,
    selectedCatalog,
    selectedCatalogKey,
    options,
    strategyParameterValues,
    parametersValid,
    loadingProfileParameters,
    profileRoleEnabled,
    defaultCatalogKey: () => defaultCatalogKey(),
  });
  const {
    assignments,
    activeRoleKey,
    roleEnabledStates,
    drafts,
    persistDraft,
    mappingOpen,
    savingProfile,
  } = mapping;

  const visibleGenerationParameters = computed(() => {
    const enabled = selectedProfile.value
      ? profileRoleEnabled.value
      : roleEnabledStates.value;
    return (selectedStrategy.value?.generation_parameters ?? []).filter(
      (parameter) =>
        !parameter.visible_for_roles?.length ||
        parameter.visible_for_roles.some(
          (roleKey) => enabled[roleKey] !== false,
        ),
    );
  });

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

  const chooseStrategy = (strategy: StrategyOption, planId?: string) => {
    if (operationActive.value) return;
    persistDraft();
    persistParameterDraft();
    selectedStrategyHash.value = strategy.summary.package_hash;
    selectedProfileDetail.value = null;
    localStorage.setItem(
      "verdflow.dts-live.strategy",
      selectedStrategyHash.value,
    );
    const profiles = strategy.profiles;
    const remembered = localStorage.getItem(profileStorageKey(strategy));
    const preferred =
      profiles.find(
        (profile) => profileKey(profile) === (planId ?? remembered),
      ) ?? profiles[0];
    selectedProfileKey.value = preferred ? profileKey(preferred) : "";
    if (preferred) {
      selectedCatalogKey.value = catalogKeyForProfile(preferred);
      assignments.value = {};
      mapping.draftSourceProfile.value = null;
    } else {
      const draft = drafts.get(strategy.summary.package_hash);
      selectedCatalogKey.value = draft?.catalogKey ?? defaultCatalogKey();
      assignments.value = cloneAssignments(draft?.assignments ?? {});
      mapping.draftSourceProfile.value = draft?.sourceProfile ?? null;
    }
    activeRoleKey.value = strategy.roles[0]?.role_key ?? "";
    restoreParameterDraft(strategy);
    return loadSelectedProfileParameters();
  };

  const chooseStrategyHash = (packageHash: string) => {
    const strategy = options.value.strategies.find(
      (item) => item.summary.package_hash === packageHash,
    );
    if (strategy) chooseStrategy(strategy);
  };

  const chooseProfile = (key: string) => {
    if (operationActive.value) return;
    selectedProfileKey.value = key;
    selectedProfileDetail.value = null;
    const profile = selectedProfile.value;
    if (!selectedStrategy.value) return;
    if (!profile) {
      profileParameterEpoch++;
      loadingProfileParameters.value = false;
      const draft = drafts.get(selectedStrategy.value.summary.package_hash);
      selectedCatalogKey.value = draft?.catalogKey ?? defaultCatalogKey();
      assignments.value = cloneAssignments(draft?.assignments ?? {});
      mapping.draftSourceProfile.value = draft?.sourceProfile ?? null;
      localStorage.removeItem(profileStorageKey(selectedStrategy.value));
      return;
    }
    selectedCatalogKey.value = catalogKeyForProfile(profile);
    assignments.value = {};
    mapping.draftSourceProfile.value = null;
    localStorage.setItem(profileStorageKey(selectedStrategy.value), key);
    void loadSelectedProfileParameters();
  };

  const loadSelectedProfileParameters = async () => {
    const epoch = ++profileParameterEpoch;
    const profile = selectedProfile.value;
    const strategy = selectedStrategy.value;
    loadingProfileParameters.value = Boolean(profile);
    if (!profile || !strategy) return;
    try {
      const detail = await profileDetail(profile.profile_id);
      if (
        epoch !== profileParameterEpoch ||
        selectedProfileKey.value !== profile.profile_id ||
        selectedStrategyHash.value !== strategy.summary.package_hash
      )
        return;
      profileRoleEnabled.value = Object.fromEntries(
        strategy.roles.map((role) => [
          role.role_key,
          Boolean(detail.assignments[role.role_key]) &&
            detail.assignments[role.role_key].enabled !== false,
        ]),
      );
      selectedProfileDetail.value = detail;
      strategyParameterValues.value = Object.fromEntries(
        strategy.generation_parameters.map((parameter) => [
          parameter.key,
          detail.strategy_parameters?.[parameter.key] ?? parameter.default,
        ]),
      );
      persistParameterDraft();
    } catch (error) {
      if (epoch !== profileParameterEpoch) return;
      selectedProfileKey.value = "";
      errorMessage.value = readableError(error);
    } finally {
      if (epoch === profileParameterEpoch)
        loadingProfileParameters.value = false;
    }
  };

  const buildRunConfiguration = (): SelectionConfiguration | null => {
    const strategy = selectedStrategy.value;
    if (!strategy) return null;
    const profile = selectedProfile.value;
    const catalog = selectedCatalog.value;
    const runConfiguration = {
      strategy_package_hash: strategy.summary.package_hash,
      ...(randomSeed.value === null ? {} : { random_seed: randomSeed.value }),
      generation_args: {
        strategy_parameters: Object.fromEntries(
          strategy.generation_parameters.map((parameter) => [
            parameter.key,
            strategyParameterValues.value[parameter.key] ?? parameter.default,
          ]),
        ),
      },
    };
    const selfContained = Object.values(assignments.value).every((assignment) =>
      assignment.candidates.every((candidate) => Boolean(candidate.target_asset_ref)));
    const sourceProfile = profile ?? (!selfContained ? mapping.draftSourceProfile.value : null);
    return sourceProfile
      ? {
          ...runConfiguration,
          profile_id: sourceProfile.profile_id,
          ...(!profile
            ? { assignments: cloneAssignments(assignments.value) }
            : {}),
        }
      : {
          ...runConfiguration,
          catalog_id: selfContained ? undefined : catalog?.catalog_id,
          assignments: assignments.value,
        };
  };
  const availablePlans = computed(() =>
    options.value.strategies.flatMap((strategy) =>
      strategy.profiles
        .filter((profile) => !profile.strategy_resolution_issue &&
          (!profile.resolved_strategy_hash || profile.resolved_strategy_hash === strategy.summary.package_hash))
        .map((profile) => ({ profile, strategy })),
    ),
  );
  const choosePlan = async (id: string) => {
    if (operationActive.value) return;
    const plan = availablePlans.value.find(
      (item) => item.profile.profile_id === id,
    );
    if (plan) await chooseStrategy(plan.strategy, id);
  };

  const refreshConfigurationOptions = async (reportError = true) => {
    if (
      refreshingConfiguration.value ||
      loading.value ||
      mappingOpen.value ||
      savingProfile.value ||
      operationActive.value
    )
      return;
    refreshingConfiguration.value = true;
    try {
      const previousStrategyHash = selectedStrategyHash.value;
      const previousProfileKey = selectedProfileKey.value;
      const refreshed = await configurationOptions();
      options.value = refreshed;

      const retainedStrategy = refreshed.strategies.find(
        (item) => item.summary.package_hash === previousStrategyHash,
      );
      if (!retainedStrategy) {
        const fallback = strategyFamilies.value[0]?.selected;
        if (fallback) chooseStrategy(fallback);
        else {
          selectedStrategyHash.value = "";
          selectedProfileKey.value = "";
          selectedCatalogKey.value = "";
          assignments.value = {};
        }
        return;
      }

      if (
        previousProfileKey &&
        !retainedStrategy.profiles.some(
          (profile) => profileKey(profile) === previousProfileKey,
        )
      ) {
        selectedProfileKey.value = "";
        const draft = drafts.get(previousStrategyHash);
        selectedCatalogKey.value = draft?.catalogKey ?? defaultCatalogKey();
        assignments.value = cloneAssignments(draft?.assignments ?? {});
      }
      if (
        !retainedStrategy.roles.some(
          (role) => role.role_key === activeRoleKey.value,
        )
      ) {
        activeRoleKey.value = retainedStrategy.roles[0]?.role_key ?? "";
      }
    } catch (error) {
      if (reportError) errorMessage.value = readableError(error);
    } finally {
      refreshingConfiguration.value = false;
    }
  };

  const defaultCatalogKey = () => {
    const catalog = options.value.catalogs[0];
    return catalog ? catalogKey(catalog) : "";
  };

  const catalogKeyForProfile = (profile: ProfileSummary) => {
    if (!profile.catalog_id) return "";
    const catalog = options.value.catalogs.find(
      (item) => item.catalog_id === profile.catalog_id,
    );
    return catalog ? catalogKey(catalog) : "";
  };

  const initializeOptions = async () => {
    options.value = await configurationOptions();
    const remembered = localStorage.getItem("verdflow.dts-live.strategy");
    const initialStrategy =
      options.value.strategies.find(
        (item) => item.summary.package_hash === remembered,
      ) ?? strategyFamilies.value[0]?.selected;
    if (initialStrategy) chooseStrategy(initialStrategy);
  };

  let configurationTimer: number | undefined;
  onMounted(() => {
    configurationTimer = window.setInterval(() => {
      if (document.visibilityState === "visible")
        void refreshConfigurationOptions(false);
    }, 5000);
  });
  onBeforeUnmount(() => {
    profileParameterEpoch++;
    if (configurationTimer) window.clearInterval(configurationTimer);
  });
  return {
    ...parameters,
    ...mapping,
    initializeOptions,
    options,
    selectedStrategyHash,
    selectedProfileKey,
    selectedCatalogKey,
    profileRoleEnabled,
    selectedProfileDetail,
    loadingProfileParameters,
    refreshingConfiguration,
    selectedStrategy,
    strategyFamilies,
    selectedStrategyVersions,
    selectedProfile,
    availablePlans,
    choosePlan,
    selectedCatalog,
    visibleGenerationParameters,
    parameterSummary,
    chooseStrategy,
    chooseStrategyHash,
    chooseProfile,
    loadSelectedProfileParameters,
    buildRunConfiguration,
    refreshConfigurationOptions,
    defaultCatalogKey,
    catalogKeyForProfile,
  };
}
export type LiveConfiguration = ReturnType<typeof useLiveConfiguration>;
