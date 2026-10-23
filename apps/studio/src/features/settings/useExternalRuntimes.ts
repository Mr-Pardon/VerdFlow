import { computed, reactive, ref, shallowRef } from "vue";

import { LocalApiClient, LocalApiError } from "../../api";
import type { ExternalRuntimeInfo } from "../../api";
import { platformBridge } from "../../platform";


export const useExternalRuntimes = () => {
  const bridge = platformBridge();
  const client = new LocalApiClient(bridge);
  const runtimes = shallowRef<ExternalRuntimeInfo[]>([]);
  const drafts = reactive<Record<string, string>>({});
  const loading = ref(true);
  const managementAvailable = ref(false);
  const action = ref<{ runtimeId: string; kind: "save" | "clear" | "browse" } | null>(null);
  const errorMessage = ref<string | null>(null);
  const notice = ref<string | null>(null);

  const availableCount = computed(
    () => runtimes.value.filter((runtime) => runtime.available).length,
  );

  const initialize = async () => {
    const connection = await bridge.getLocalApiConnection();
    managementAvailable.value =
      connection.ok && connection.value.managementAvailable;
    await refresh();
  };

  const refresh = async () => {
    loading.value = true;
    errorMessage.value = null;
    try {
      const result = await client.externalRuntimes();
      runtimes.value = result.items;
      for (const runtime of result.items) {
        drafts[runtime.runtime_id] = runtime.configured_path ?? "";
      }
    } catch (error) {
      errorMessage.value = readableError(error);
    } finally {
      loading.value = false;
    }
  };

  const save = async (runtime: ExternalRuntimeInfo, path?: string) => {
    const value = (path ?? drafts[runtime.runtime_id] ?? "").trim();
    if (!value) {
      errorMessage.value = `请选择或填写 ${runtime.display_name} 路径`;
      return false;
    }
    action.value = { runtimeId: runtime.runtime_id, kind: "save" };
    errorMessage.value = null;
    notice.value = null;
    try {
      const updated = await client.configureExternalRuntime(
        runtime.runtime_id,
        value,
      );
      updateRuntime(updated);
      drafts[runtime.runtime_id] = updated.configured_path ?? "";
      notice.value = `已更新 ${updated.display_name}`;
      return true;
    } catch (error) {
      errorMessage.value = readableError(error);
      return false;
    } finally {
      action.value = null;
    }
  };

  const browse = async (runtime: ExternalRuntimeInfo) => {
    action.value = { runtimeId: runtime.runtime_id, kind: "browse" };
    errorMessage.value = null;
    const selected = await bridge.selectExternalRuntimePath({
      runtimeId: runtime.runtime_id,
      displayName: runtime.display_name,
      pathKind: runtime.path_kind,
    });
    action.value = null;
    if (!selected.ok) {
      if (selected.reason !== "cancelled") errorMessage.value = selected.message;
      return;
    }
    drafts[runtime.runtime_id] = selected.value;
    await save(runtime, selected.value);
  };

  const clear = async (runtime: ExternalRuntimeInfo) => {
    action.value = { runtimeId: runtime.runtime_id, kind: "clear" };
    errorMessage.value = null;
    notice.value = null;
    try {
      const updated = await client.clearExternalRuntime(runtime.runtime_id);
      updateRuntime(updated);
      drafts[runtime.runtime_id] = "";
      notice.value = `${updated.display_name} 已恢复自动发现`;
      return true;
    } catch (error) {
      errorMessage.value = readableError(error);
      return false;
    } finally {
      action.value = null;
    }
  };

  const updateRuntime = (updated: ExternalRuntimeInfo) => {
    runtimes.value = runtimes.value.map((runtime) =>
      runtime.runtime_id === updated.runtime_id ? updated : runtime,
    );
  };

  return {
    action,
    availableCount,
    browse,
    clear,
    drafts,
    errorMessage,
    initialize,
    loading,
    managementAvailable,
    notice,
    refresh,
    runtimes,
    save,
  };
};

const readableError = (error: unknown) => {
  if (error instanceof LocalApiError) {
    if (error.code === "connection_failed") return "无法连接 VerdFlow 本地服务";
    if (error.code === "management_auth_required") {
      return "当前桌面应用没有外部运行环境管理权限";
    }
    return error.message;
  }
  return error instanceof Error ? error.message : "外部运行环境操作失败";
};
