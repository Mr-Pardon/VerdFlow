import { computed, ref, shallowRef } from "vue";

import { LocalApiClient, LocalApiError } from "../../api";
import type { StrategyLibraryDetail, StrategyLibrarySummary } from "../../api";
import { platformBridge } from "../../platform";
import {
  importPackagesSequentially,
  packageImportFailureMessage,
  unsupportedPackageFailures,
} from "./packageBatchImport";

export const useStrategyLibrary = () => {
  const bridge = platformBridge();
  const client = new LocalApiClient(bridge);
  const strategies = shallowRef<StrategyLibrarySummary[]>([]);
  const detail = shallowRef<StrategyLibraryDetail | null>(null);
  const selectedHash = ref("");
  const loading = ref(true);
  const loadingDetail = ref(false);
  const action = ref<"import" | "export" | "kit" | "trust" | "revoke" | "delete" | null>(null);
  const managementAvailable = ref(false);
  const errorMessage = ref<string | null>(null);
  const noticeMessage = ref<string | null>(null);
  const trustableStrategies = computed(() => strategies.value.filter(item => item.trust_state !== 'trusted' && item.trust_state !== 'revoked'));
  const trustAll = async (targets: readonly StrategyLibrarySummary[]) => {
    if (action.value !== null || !managementAvailable.value) return;
    action.value = 'trust';
    errorMessage.value = null;
    noticeMessage.value = null;
    let completed = 0;
    const failures: string[] = [];
    try {
      for (const target of targets) {
        const current = strategies.value.find(item => item.package_hash === target.package_hash);
        if (!current || current.trust_state === 'trusted' || current.trust_state === 'revoked') continue;
        try {
          const updated = await client.trustStrategy(target.package_hash);
          strategies.value = strategies.value.map(item => item.package_hash === updated.package_hash ? updated : item);
          if (detail.value?.summary.package_hash === updated.package_hash) detail.value = { ...detail.value, summary: updated };
          completed++;
        } catch (error) { failures.push(`${target.display_name} v${target.strategy_version}：${readableError(error)}`); }
      }
      noticeMessage.value = `已信任 ${completed} 个策略包`;
      if (failures.length) errorMessage.value = failures.join('\n');
    } finally { action.value = null; }
  };

  const groups = computed(() => {
    const grouped = new Map<string, StrategyLibrarySummary[]>();
    for (const strategy of strategies.value) {
      const items = grouped.get(strategy.strategy_id) ?? [];
      items.push(strategy);
      grouped.set(strategy.strategy_id, items);
    }
    return [...grouped.entries()]
      .map(([strategyId, versions]) => ({
        strategyId,
        displayName: versions[0]?.display_name ?? strategyId,
        versions: [...versions].sort((left, right) =>
          right.strategy_version.localeCompare(left.strategy_version, undefined, {
            numeric: true,
          }),
        ),
      }))
      .sort((left, right) => left.displayName.localeCompare(right.displayName)
        || left.strategyId.localeCompare(right.strategyId));
  });

  const initialize = async () => {
    const connection = await bridge.getLocalApiConnection();
    managementAvailable.value = connection.ok && connection.value.managementAvailable;
    await refresh();
  };

  const refresh = async () => {
    loading.value = true;
    errorMessage.value = null;
    try {
      const previousStrategyId = detail.value?.summary.strategy_id ?? null;
      const result = await client.strategies();
      strategies.value = result.items;
      const selectedStillExists = strategies.value.some(
        (item) => item.package_hash === selectedHash.value,
      );
      const replacement = previousStrategyId
        ? strategies.value.find((item) => item.strategy_id === previousStrategyId)
        : null;
      const next = selectedStillExists
        ? selectedHash.value
        : replacement?.package_hash ?? "";
      await selectStrategy(next);
    } catch (error) {
      errorMessage.value = readableError(error);
    } finally {
      loading.value = false;
    }
  };

  const selectStrategy = async (packageHash: string) => {
    selectedHash.value = packageHash;
    detail.value = null;
    if (!packageHash) return;
    loadingDetail.value = true;
    errorMessage.value = null;
    try {
      detail.value = await client.strategy(packageHash);
    } catch (error) {
      errorMessage.value = readableError(error);
    } finally {
      loadingDetail.value = false;
    }
  };

  const importStrategy = async () => {
    try {
      const selected = await bridge.selectSourceFiles(["vfs"]);
      if (!selected.ok) {
        if (selected.reason !== "cancelled") errorMessage.value = selected.message;
        return;
      }
      await importStrategyPaths(selected.value);
    } catch (error) {
      errorMessage.value = readableError(error);
    }
  };

  const importStrategyPaths = async (
    sourcePaths: readonly string[],
    rejectedNames: readonly string[] = [],
    pathFailures: readonly string[] = [],
  ) => {
    if (action.value !== null) return;
    action.value = "import";
    errorMessage.value = null;
    noticeMessage.value = null;
    try {
      const outcome = await importPackagesSequentially(
        sourcePaths,
        (sourcePath) => client.importStrategyPackage(sourcePath),
        readableError,
      );
      const imported = outcome.imported;
      if (imported.length) {
        selectedHash.value = imported.at(-1)?.strategy.package_hash ?? "";
        await refresh();
        const existingCount = imported.filter((item) => item.already_installed).length;
        const newCount = imported.length - existingCount;
        noticeMessage.value = `已处理 ${imported.length} 个策略包：新导入 ${newCount} 个，已存在 ${existingCount} 个${newCount ? "；新策略执行前仍需手动信任" : ""}`;
      }
      const failures = [
        ...unsupportedPackageFailures(rejectedNames, "vfs"),
        ...pathFailures.map((message, index) => ({
          ordinal: rejectedNames.length + index + 1,
          sourceName: "拖入文件",
          message,
        })),
        ...outcome.failures,
      ];
      if (failures.length && !errorMessage.value) {
        errorMessage.value = packageImportFailureMessage(
          "策略包",
          failures,
          imported.length,
        );
      }
    } catch (error) {
      errorMessage.value = readableError(error);
    } finally {
      action.value = null;
    }
  };

  const exportStrategy = async () => {
    const summary = detail.value?.summary;
    if (!summary || action.value !== null) return;
    const selected = await bridge.selectExportTarget({
      suggestedName: `${summary.strategy_id}-${summary.strategy_version}.vfs`,
      mediaType: "application/vnd.verdflow.strategy-package+zip",
    });
    if (!selected.ok) {
      if (selected.reason !== "cancelled") errorMessage.value = selected.message;
      return;
    }
    action.value = "export";
    errorMessage.value = null;
    noticeMessage.value = null;
    try {
      await client.exportStrategyPackage({
        packageHash: summary.package_hash,
        destinationPath: selected.value,
      });
      noticeMessage.value = `已导出 ${summary.display_name} 的精确策略包`;
    } catch (error) {
      errorMessage.value = readableError(error);
    } finally {
      action.value = null;
    }
  };

  const exportAuthoringKit = async () => {
    if (action.value !== null) return;
    const selected = await bridge.selectExportTarget({
      suggestedName: "vfs-author.zip",
      mediaType: "application/vnd.verdflow.authoring-kit+zip",
    });
    if (!selected.ok) {
      if (selected.reason !== "cancelled") errorMessage.value = selected.message;
      return;
    }
    action.value = "kit";
    errorMessage.value = null;
    noticeMessage.value = null;
    try {
      await client.exportAuthoringKit({
        skillId: "vfs-author",
        destinationPath: selected.value,
      });
      noticeMessage.value = "VFS Author 工具包已导出";
    } catch (error) {
      errorMessage.value = readableError(error);
    } finally {
      action.value = null;
    }
  };

  const setTrust = async (trusted: boolean) => {
    if (!detail.value) return;
    action.value = trusted ? "trust" : "revoke";
    errorMessage.value = null;
    try {
      const updated = trusted
        ? await client.trustStrategy(detail.value.summary.package_hash)
        : await client.revokeStrategyTrust(detail.value.summary.package_hash);
      strategies.value = strategies.value.map((item) =>
        item.package_hash === updated.package_hash ? updated : item,
      );
      detail.value = { ...detail.value, summary: updated };
    } catch (error) {
      errorMessage.value = readableError(error);
    } finally {
      action.value = null;
    }
  };

  const uninstall = async () => {
    if (!detail.value) return;
    action.value = "delete";
    errorMessage.value = null;
    try {
      await client.uninstallStrategy(detail.value.summary.package_hash);
      selectedHash.value = "";
      detail.value = null;
      await refresh();
    } catch (error) {
      errorMessage.value = readableError(error);
    } finally {
      action.value = null;
    }
  };

  return {
    action,
    detail,
    errorMessage,
    exportAuthoringKit,
    exportStrategy,
    groups,
    importStrategy,
    importStrategyPaths,
    initialize,
    loading,
    loadingDetail,
    managementAvailable,
    noticeMessage,
    refresh,
    selectStrategy,
    selectedHash,
    setTrust,
    trustAll,
    trustableStrategies,
    strategies,
    uninstall,
    canExportTarget: bridge.capabilities.selectExportTarget,
    canSelectSourceFiles: bridge.capabilities.selectSourceFiles,
  };
};

const readableError = (error: unknown) => {
  if (error instanceof LocalApiError) {
    return error.code === "connection_failed"
      ? "无法连接 VerdFlow 本地服务"
      : error.message;
  }
  return error instanceof Error ? error.message : "策略库操作失败";
};
