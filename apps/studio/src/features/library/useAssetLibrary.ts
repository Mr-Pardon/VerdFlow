import { computed, ref, shallowRef } from "vue";

import { LocalApiClient, LocalApiError } from "../../api";
import type {
  AssetCatalogAssetInfo,
  AssetCatalogCollectionInfo,
  AssetCatalogDetail,
  AssetCatalogDocument,
  AssetCatalogSummary,
} from "../../api";
import { platformBridge } from "../../platform";
import {
  importPackagesSequentially,
  packageImportFailureMessage,
  unsupportedPackageFailures,
} from "./packageBatchImport";

export type AssetTargetType = "dts" | "blender";

export interface CreateAssetCatalogInput {
  targetType: AssetTargetType;
  catalogId: string;
  displayName: string;
  providerId: string;
}

export const useAssetLibrary = () => {
  const bridge = platformBridge();
  const client = new LocalApiClient(bridge);
  const targetType = ref<AssetTargetType>("dts");
  const catalogs = shallowRef<AssetCatalogSummary[]>([]);
  const catalogDetail = shallowRef<AssetCatalogDetail | null>(null);
  const assets = shallowRef<AssetCatalogAssetInfo[]>([]);
  const collections = shallowRef<AssetCatalogCollectionInfo[]>([]);
  const selectedCatalogKey = ref("");
  const totalAssets = ref(0);
  const selectedCollectionId = ref("");
  const assetQuery = ref("");
  const nextAssetOrdinal = ref<number | null>(null);
  const loading = ref(true);
  const loadingContent = ref(false);
  const loadingMore = ref(false);
  const mutating = ref(false);
  const errorMessage = ref<string | null>(null);
  const noticeMessage = ref<string | null>(null);

  const selectedCatalog = computed(
    () => catalogs.value.find((item) => catalogKey(item) === selectedCatalogKey.value) ?? null,
  );
  const sortedCatalogs = computed(() =>
    [...catalogs.value]
      .sort((left, right) => left.display_name.localeCompare(right.display_name)),
  );

  const initialize = () => refreshTarget();

  const setTarget = async (target: AssetTargetType) => {
    if (targetType.value === target) return;
    targetType.value = target;
    await refreshTarget();
  };

  const refreshTarget = async () => {
    loading.value = true;
    errorMessage.value = null;
    assets.value = [];
    collections.value = [];
    catalogDetail.value = null;
    try {
      const page = await client.assetCatalogs(targetType.value);
      catalogs.value = page.items;
      const preferred = page.items.find(
        (item) => catalogKey(item) === selectedCatalogKey.value,
      ) ?? (page.items.length === 1 ? page.items[0] : undefined);
      if (preferred) await selectCatalog(catalogKey(preferred));
      else {
        selectedCatalogKey.value = "";
        selectedCollectionId.value = "";
        assetQuery.value = "";
      }
    } catch (error) {
      errorMessage.value = readableError(error);
    } finally {
      loading.value = false;
    }
  };

  const selectCatalog = async (key: string) => {
    selectedCatalogKey.value = key;
    selectedCollectionId.value = "";
    assetQuery.value = "";
    assets.value = [];
    collections.value = [];
    catalogDetail.value = null;
    const catalog = selectedCatalog.value;
    if (!catalog) return;
    loadingContent.value = true;
    errorMessage.value = null;
    try {
      const [detail, page] = await Promise.all([
        client.assetCatalog(catalog.target_type, catalog.catalog_id),
        client.assetCatalogAssets({
          targetType: catalog.target_type,
          catalogId: catalog.catalog_id,
          limit: 200,
        }),
      ]);
      catalogDetail.value = detail;
      applyAssetPage(page);
    } catch (error) {
      errorMessage.value = readableError(error);
    } finally {
      loadingContent.value = false;
    }
  };

  const createAssetCatalog = async (input: CreateAssetCatalogInput) => {
    if (mutating.value) return null;
    mutating.value = true;
    errorMessage.value = null;
    try {
      const result = await client.saveAssetCatalog({
        targetType: input.targetType,
        catalogId: input.catalogId,
        expectedGeneration: 0,
        document: {
          schema: "verdflow.target-asset-catalog",
          version: "1",
          catalog_id: input.catalogId,
          target_type: input.targetType,
          provider_id: input.providerId,
          display_name: input.displayName,
          assets: [],
          collections: [],
        },
      });
      targetType.value = input.targetType;
      selectedCatalogKey.value = `${input.targetType}:${input.catalogId}`;
      await refreshTarget();
      noticeMessage.value = `已建立 ${input.displayName}，可以开始添加植被资产`;
      return result;
    } catch (error) {
      errorMessage.value = readableError(error);
      return null;
    } finally {
      mutating.value = false;
    }
  };

  const saveAssetCatalog = async (document: AssetCatalogDocument) => {
    const current = catalogDetail.value;
    if (!current || mutating.value) return null;
    mutating.value = true;
    errorMessage.value = null;
    try {
      const result = await client.saveAssetCatalog({
        targetType: current.info.target_type,
        catalogId: current.info.catalog_id,
        document,
        expectedGeneration: current.info.generation,
      });
      catalogDetail.value = result;
      noticeMessage.value = "资产库已保存";
      await refreshTarget();
      return result;
    } catch (error) {
      errorMessage.value = readableError(error);
      return null;
    } finally {
      mutating.value = false;
    }
  };

  const deleteSelectedCatalog = async () => {
    const catalog = selectedCatalog.value;
    if (!catalog || mutating.value) return false;
    mutating.value = true;
    errorMessage.value = null;
    try {
      const result = await client.deleteAssetCatalog({
        targetType: catalog.target_type,
        catalogId: catalog.catalog_id,
      });
      noticeMessage.value = result.affected_profiles
        ? `资产库已删除；${result.affected_profiles} 个植被搭配将显示非阻塞提示`
        : "资产库已删除";
      selectedCatalogKey.value = "";
      await refreshTarget();
      return true;
    } catch (error) {
      errorMessage.value = readableError(error);
      return false;
    } finally {
      mutating.value = false;
    }
  };

  const importThumbnail = async () => {
    const selected = await bridge.selectSourceFile(["png", "jpg", "jpeg", "webp"]);
    if (!selected.ok) {
      if (selected.reason !== "cancelled") errorMessage.value = selected.message;
      return null;
    }
    mutating.value = true;
    errorMessage.value = null;
    try {
      const resource = await client.importLibraryThumbnail(selected.value);
      return `verdflow-resource:${resource.resource_id}`;
    } catch (error) {
      errorMessage.value = readableError(error);
      return null;
    } finally {
      mutating.value = false;
    }
  };

  const importCatalogPackage = async () => {
    if (mutating.value) return false;
    const selected = await bridge.selectSourceFiles(["vfa"]);
    if (!selected.ok) {
      if (selected.reason !== "cancelled") errorMessage.value = selected.message;
      return false;
    }
    return importCatalogPackagePaths(selected.value);
  };

  const importCatalogPackagePaths = async (
    sourcePaths: readonly string[],
    rejectedNames: readonly string[] = [],
    pathFailures: readonly string[] = [],
  ) => {
    if (mutating.value) return false;
    mutating.value = true;
    errorMessage.value = null;
    noticeMessage.value = null;
    try {
      const outcome = await importPackagesSequentially(
        sourcePaths,
        async (sourcePath) => {
          const result = await client.importAssetCatalogPackage(sourcePath);
          if (result.catalog.target_type !== "dts" && result.catalog.target_type !== "blender") {
            throw new Error(`暂不支持目标类型 ${result.catalog.target_type}`);
          }
          return result;
        },
        readableError,
      );
      const imported = outcome.imported;
      if (imported.length) {
        const last = imported.at(-1);
        if (last) {
          const importedTarget = last.catalog.target_type;
          if (importedTarget !== "dts" && importedTarget !== "blender") {
            throw new Error(`暂不支持目标类型 ${importedTarget}`);
          }
          targetType.value = importedTarget;
          selectedCatalogKey.value = catalogKey(last.catalog);
        }
        await refreshTarget();
        const existingCount = imported.filter((item) => item.already_installed).length;
        noticeMessage.value = `已处理 ${imported.length} 个资产包：新导入 ${imported.length - existingCount} 个，已存在 ${existingCount} 个`;
      }
      const failures = [
        ...unsupportedPackageFailures(rejectedNames, "vfa"),
        ...pathFailures.map((message, index) => ({
          ordinal: rejectedNames.length + index + 1,
          sourceName: "拖入文件",
          message,
        })),
        ...outcome.failures,
      ];
      if (failures.length && !errorMessage.value) {
        errorMessage.value = packageImportFailureMessage(
          "资产包",
          failures,
          imported.length,
        );
      }
      return imported.length > 0;
    } catch (error) {
      errorMessage.value = readableError(error);
      return false;
    } finally {
      mutating.value = false;
    }
  };

  const exportSelectedCatalog = async () => {
    const catalog = selectedCatalog.value;
    if (!catalog || mutating.value) return false;
    const selected = await bridge.selectExportTarget({
      suggestedName: `${catalog.catalog_id}.vfa`,
      mediaType: "application/vnd.verdflow.asset-catalog+zip",
    });
    if (!selected.ok) {
      if (selected.reason !== "cancelled") errorMessage.value = selected.message;
      return false;
    }
    mutating.value = true;
    errorMessage.value = null;
    try {
      const result = await client.exportAssetCatalogPackage({
        targetType: catalog.target_type,
        catalogId: catalog.catalog_id,
        destinationPath: selected.value,
      });
      noticeMessage.value = `已导出 ${result.catalog.display_name} 的固定快照`;
      return true;
    } catch (error) {
      errorMessage.value = readableError(error);
      return false;
    } finally {
      mutating.value = false;
    }
  };

  const exportAuthoringKit = async () => {
    if (mutating.value) return false;
    const selected = await bridge.selectExportTarget({
      suggestedName: "vfa-author.zip",
      mediaType: "application/vnd.verdflow.authoring-kit+zip",
    });
    if (!selected.ok) {
      if (selected.reason !== "cancelled") errorMessage.value = selected.message;
      return false;
    }
    mutating.value = true;
    errorMessage.value = null;
    noticeMessage.value = null;
    try {
      await client.exportAuthoringKit({
        skillId: "vfa-author",
        destinationPath: selected.value,
      });
      noticeMessage.value = "VFA Author 工具包已导出";
      return true;
    } catch (error) {
      errorMessage.value = readableError(error);
      return false;
    } finally {
      mutating.value = false;
    }
  };

  const refreshAssets = async () => {
    const catalog = selectedCatalog.value;
    if (!catalog) return;
    loadingContent.value = true;
    errorMessage.value = null;
    try {
      const page = await client.assetCatalogAssets({
        targetType: catalog.target_type,
        catalogId: catalog.catalog_id,
        collectionId: selectedCollectionId.value || undefined,
        query: assetQuery.value.trim() || undefined,
        limit: 200,
      });
      applyAssetPage(page);
    } catch (error) {
      errorMessage.value = readableError(error);
    } finally {
      loadingContent.value = false;
    }
  };

  const setCollection = async (collectionId: string) => {
    if (selectedCollectionId.value === collectionId) return;
    selectedCollectionId.value = collectionId;
    await refreshAssets();
  };

  const setAssetQuery = async (query: string) => {
    const normalized = query.trim();
    if (assetQuery.value === normalized) return;
    assetQuery.value = normalized;
    await refreshAssets();
  };

  const loadMoreAssets = async () => {
    const catalog = selectedCatalog.value;
    if (!catalog || nextAssetOrdinal.value === null || loadingMore.value) return;
    loadingMore.value = true;
    try {
      const page = await client.assetCatalogAssets({
        targetType: catalog.target_type,
        catalogId: catalog.catalog_id,
        afterOrdinal: nextAssetOrdinal.value,
        collectionId: selectedCollectionId.value || undefined,
        query: assetQuery.value || undefined,
        limit: 200,
      });
      assets.value = [...assets.value, ...page.items];
      nextAssetOrdinal.value = page.next_after_ordinal;
    } catch (error) {
      errorMessage.value = readableError(error);
    } finally {
      loadingMore.value = false;
    }
  };

  const applyAssetPage = (page: Awaited<ReturnType<typeof client.assetCatalogAssets>>) => {
    assets.value = page.items;
    collections.value = page.collections;
    totalAssets.value = page.total_count;
    nextAssetOrdinal.value = page.next_after_ordinal;
  };

  return {
    assets,
    assetQuery,
    catalogDetail,
    catalogs,
    collections,
    canSelectSourceFiles: bridge.capabilities.selectSourceFiles,
    createAssetCatalog,
    deleteSelectedCatalog,
    errorMessage,
    exportAuthoringKit,
    exportSelectedCatalog,
    importCatalogPackage,
    importCatalogPackagePaths,
    importThumbnail,
    initialize,
    loadMoreAssets,
    loading,
    loadingContent,
    loadingMore,
    mutating,
    nextAssetOrdinal,
    noticeMessage,
    refreshAssets,
    refreshTarget,
    saveAssetCatalog,
    selectCatalog,
    selectedCatalog,
    selectedCatalogKey,
    selectedCollectionId,
    setAssetQuery,
    setCollection,
    setTarget,
    sortedCatalogs,
    targetType,
    totalAssets,
  };
};

export const catalogKey = (catalog: Pick<AssetCatalogSummary, "target_type" | "catalog_id">) =>
  `${catalog.target_type}:${catalog.catalog_id}`;

const readableError = (error: unknown) => {
  if (error instanceof LocalApiError) {
    return error.code === "connection_failed"
      ? "无法连接 VerdFlow 本地服务"
      : error.message;
  }
  return error instanceof Error ? error.message : "资产库读取失败";
};
