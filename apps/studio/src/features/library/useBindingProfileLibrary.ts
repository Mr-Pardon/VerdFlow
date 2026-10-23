import { computed, onBeforeUnmount, ref, shallowRef } from "vue";

import { LocalApiClient, LocalApiError } from "../../api";
import type {
  AssetCatalogAssetInfo,
  AssetCatalogSummary,
  RoleBindingProfileDetail,
  StrategyLibraryDetail,
} from "../../api";
import { platformBridge } from "../../platform";
import { enrichAssetThumbnails } from "./assetPresentation";

export type BindingTargetType = "dts" | "blender";

export const useBindingProfileLibrary = (input: {
  targetType: BindingTargetType;
  profileId: () => string;
}) => {
  const client = new LocalApiClient(platformBridge());
  const targetType = ref<BindingTargetType>(input.targetType);
  const strategySummary = shallowRef<
    import("../../api").StrategyLibrarySummary | null
  >(null);
  const profileDetail = shallowRef<RoleBindingProfileDetail | null>(null);
  const roleNames = shallowRef<Record<string, string>>({});
  const generationParameters = shallowRef<
    import("../../api").StrategyGenerationParameterInfo[]
  >([]);
  const assetDetails = shallowRef<Record<string, AssetCatalogAssetInfo>>({});
  const sourceCatalogs = shallowRef<AssetCatalogSummary[] | null>([]);
  const loading = ref(true);
  const loadingDetail = ref(false);
  const errorMessage = ref<string | null>(null);
  const statusMessage = ref<string | null>(null);
  const managementAvailable = ref(false);
  const mutating = ref(false);
  let selectionEpoch = 0;

  const selectedProfile = computed(() => profileDetail.value?.summary ?? null);
  const strategyDisplayName = computed(() => strategySummary.value?.display_name || "策略暂不可用");
  const catalogDisplayName = computed(() => {
    if (!profileDetail.value) return "未关联资产库";
    const ids = profileCatalogIds(profileDetail.value);
    if (!ids.length) return "未关联资产库";
    if (!sourceCatalogs.value) return "来源资产库暂不可用";
    const names = ids.map(id => sourceCatalogs.value?.find(catalog => catalog.catalog_id === id)?.display_name || "来源资产库已移除");
    return [...new Set(names)].join("、");
  });
  onBeforeUnmount(() => {
    selectionEpoch++;
  });
  const syncableMetadataCount = computed(
    () =>
      Object.values(profileDetail.value?.catalog_drift ?? {}).filter(
        (status) => status === "metadata_changed",
      ).length,
  );

  const initialize = async () => {
    const connection = await platformBridge().getLocalApiConnection();
    managementAvailable.value =
      connection.ok && connection.value.managementAvailable;
    await refreshTarget();
  };

  const refreshTarget = async () => {
    const epoch = ++selectionEpoch;
    loading.value = true;
    errorMessage.value = null;
    statusMessage.value = null;
    profileDetail.value = null;
    roleNames.value = {};
    generationParameters.value = [];
    assetDetails.value = {};
    sourceCatalogs.value = [];
    loadingDetail.value = true;
    strategySummary.value = null;
    try {
      const detail = await client.roleBindingProfile(input.profileId());
      const [strategy, resolvedAssets, catalogs] = await Promise.all([
        resolveStrategyDetail(client, detail).catch(() => null),
        resolveLinkedAssets(client, detail).catch(() => ({})),
        profileCatalogIds(detail).length
          ? client.assetCatalogs(detail.summary.target_type).then(page => page.items).catch(() => null)
          : Promise.resolve([]),
      ]);
      if (epoch !== selectionEpoch) return;
      profileDetail.value = detail;
      strategySummary.value = strategy?.summary ?? null;
      generationParameters.value = strategy?.generation_parameters ?? [];
      roleNames.value = Object.fromEntries(
        (strategy?.roles ?? []).map((role) => [
          role.role_key,
          role.display_name,
        ]),
      );
      assetDetails.value = resolvedAssets;
      sourceCatalogs.value = catalogs;
    } catch (error) {
      if (epoch === selectionEpoch) errorMessage.value = readableError(error);
    } finally {
      if (epoch === selectionEpoch) {
        loading.value = false;
        loadingDetail.value = false;
      }
    }
  };

  const profileSaved = async (detail: RoleBindingProfileDetail) => {
    await refreshTarget();
    statusMessage.value = `已保存种植方案“${detail.summary.display_name}”`;
  };

  const deleteSelectedProfile = async () => {
    const profile = selectedProfile.value;
    if (!profile || mutating.value) return;
    mutating.value = true;
    errorMessage.value = null;
    try {
      await client.deleteRoleBindingProfile(profile.profile_id);
      profileDetail.value = null;
      statusMessage.value = `已删除种植方案“${profile.display_name}”`;
    } catch (error) {
      errorMessage.value = readableError(error);
    } finally {
      mutating.value = false;
    }
  };

  const syncSelectedAssetMetadata = async () => {
    const profile = profileDetail.value;
    if (!profile || !syncableMetadataCount.value || mutating.value) return;
    const syncedCount = syncableMetadataCount.value;
    mutating.value = true;
    errorMessage.value = null;
    try {
      await client.syncRoleBindingProfileAssets(
        profile.summary.profile_id,
        profile.summary.generation,
      );
      await refreshTarget();
      statusMessage.value = `已同步 ${syncedCount} 项资产信息`;
    } catch (error) {
      errorMessage.value = readableError(error);
    } finally {
      mutating.value = false;
    }
  };

  const exportSelected = async () => {
    const bridge = platformBridge();
    const profile = selectedProfile.value;
    if (!profile) return;
    if (!managementAvailable.value || !bridge.capabilities.selectExportTarget) {
      errorMessage.value = "当前运行方式不支持导出种植方案";
      return;
    }
    const selected = await bridge.selectExportTarget({
      suggestedName: `${profile.profile_id}.vfp`,
      mediaType: "application/vnd.verdflow.role-binding-profile+zip",
    });
    if (!selected.ok) {
      if (selected.reason !== "cancelled")
        errorMessage.value = selected.message;
      return;
    }
    mutating.value = true;
    errorMessage.value = null;
    try {
      const exported = await client.exportRoleBindingProfilePackage({
        profileId: profile.profile_id,
        destinationPath: selected.value,
      });
      statusMessage.value = `已导出到 ${exported.destination_path}`;
    } catch (error) {
      errorMessage.value = readableError(error);
    } finally {
      mutating.value = false;
    }
  };

  return {
    assetDetails,
    catalogDisplayName,
    deleteSelectedProfile,
    errorMessage,
    exportSelected,
    generationParameters,
    initialize,
    loading,
    loadingDetail,
    managementAvailable,
    mutating,
    profileDetail,
    profileSaved,
    refreshTarget,
    roleNames,
    selectedProfile,
    statusMessage,
    strategySummary,
    strategyDisplayName,
    syncableMetadataCount,
    syncSelectedAssetMetadata,
    targetType,
  };
};

const profileCatalogIds = (detail: RoleBindingProfileDetail): string[] => [
  ...new Set([
    detail.summary.catalog_id,
    ...Object.values(detail.assignments).flatMap(assignment =>
      Array.isArray(assignment.candidates) ? assignment.candidates.flatMap(candidate => {
        if (!candidate || typeof candidate !== "object") return [];
        const link = (candidate as Record<string, unknown>).catalog_link;
        return link && typeof link === "object" ? [(link as Record<string, unknown>).catalog_id] : [];
      }) : [],
    ),
  ].filter((id): id is string => typeof id === "string" && id.length > 0)),
];

const resolveStrategyDetail = async (
  client: LocalApiClient,
  detail: RoleBindingProfileDetail,
): Promise<StrategyLibraryDetail | null> => {
  const strategies = await client.strategies();
  const summary = strategies.items.find(
    (strategy) =>
      detail.summary.resolved_strategy_hash
        ? strategy.package_hash === detail.summary.resolved_strategy_hash
        : strategy.strategy_id === detail.summary.strategy_id &&
          strategy.strategy_version === detail.summary.strategy_version,
  );
  return summary ? client.strategy(summary.package_hash) : null;
};

const resolveLinkedAssets = async (
  client: LocalApiClient,
  detail: RoleBindingProfileDetail,
): Promise<Record<string, AssetCatalogAssetInfo>> => {
  if (detail.assets) return Object.fromEntries(detail.assets.map((asset) => [asset.asset_id, asset]));
  const catalogId = detail.summary.catalog_id;
  if (!catalogId) return {};
  const ids = profileAssetIds(detail.assignments);
  const entries = await Promise.all(
    ids.map(async (assetId) => {
      try {
        const page = await client.assetCatalogAssets({
          targetType: detail.summary.target_type,
          catalogId,
          query: assetId,
          limit: 20,
        });
        const asset = page.items.find((item) => item.asset_id === assetId);
        if (asset) return asset;
      } catch {
        // The optional Catalog link may no longer exist.
      }
      return null;
    }),
  );
  const exact = entries.filter(
    (item): item is AssetCatalogAssetInfo => item !== null,
  );
  const presented = await enrichAssetThumbnails(client, exact, {
    targetType: detail.summary.target_type,
    catalogId,
  }).catch(() => exact);
  return Object.fromEntries(presented.map((asset) => [asset.asset_id, asset]));
};

const profileAssetIds = (
  assignments: Record<string, Record<string, unknown>>,
) => [
  ...new Set(
    Object.values(assignments).flatMap((assignment) =>
      Array.isArray(assignment.candidates)
        ? assignment.candidates.flatMap((candidate) => {
            if (!candidate || typeof candidate !== "object") return [];
            const assetId = (candidate as Record<string, unknown>).asset_id;
            return typeof assetId === "string" ? [assetId] : [];
          })
        : [],
    ),
  ),
];

const readableError = (error: unknown) => {
  if (error instanceof LocalApiError) {
    return error.code === "connection_failed"
      ? "无法连接 VerdFlow 本地服务"
      : error.message;
  }
  return error instanceof Error ? error.message : "种植方案读取失败";
};
