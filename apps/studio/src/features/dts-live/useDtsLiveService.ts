import { computed, ref, shallowRef } from "vue";

import { LocalApiClient, LocalApiError } from "../../api";
import type { DtsLiveServiceStatus } from "../../api";
import { platformBridge } from "../../platform";

export type DtsLiveAction = "start" | "relaunch" | "stop" | "refresh";

export const useDtsLiveService = () => {
  const bridge = platformBridge();
  const client = new LocalApiClient(bridge);
  const status = shallowRef<DtsLiveServiceStatus | null>(null);
  const managementAvailable = ref(false);
  const action = ref<DtsLiveAction | null>(null);
  const errorMessage = ref<string | null>(null);
  let refreshTimer: number | undefined;

  const running = computed(
    () =>
      status.value !== null &&
      !["stopped", "failed"].includes(status.value.state),
  );

  const initialize = async () => {
    const connection = await bridge.getLocalApiConnection();
    managementAvailable.value =
      connection.ok && connection.value.managementAvailable;
    await refresh();
  };

  const refresh = async (showBusy = true) => {
    if (showBusy) action.value = "refresh";
    try {
      status.value = await client.dtsLiveStatus();
      errorMessage.value = null;
      syncRefreshTimer();
    } catch (error) {
      errorMessage.value = readableError(error);
    } finally {
      if (showBusy) action.value = null;
    }
  };

  const start = async () => {
    if (action.value) return;
    action.value = "start";
    errorMessage.value = null;
    try {
      const started = await client.startDtsLive();
      status.value = started;
      await launchExplorer(started);
      syncRefreshTimer();
    } catch (error) {
      const message = readableError(error);
      try {
        status.value = await client.dtsLiveStatus();
        syncRefreshTimer();
      } catch {
        // Preserve the original launch/start failure; it is more actionable.
      }
      errorMessage.value = message;
    } finally {
      action.value = null;
    }
  };

  const relaunch = async () => {
    if (action.value) return;
    action.value = "relaunch";
    errorMessage.value = null;
    try {
      const relaunched = await client.relaunchDtsLive();
      status.value = relaunched;
      await launchExplorer(relaunched);
      syncRefreshTimer();
    } catch (error) {
      const message = readableError(error);
      try {
        status.value = await client.dtsLiveStatus();
        syncRefreshTimer();
      } catch {
        // Preserve the original relaunch failure; it is more actionable.
      }
      errorMessage.value = message;
    } finally {
      action.value = null;
    }
  };

  const stop = async () => {
    if (action.value) return;
    action.value = "stop";
    errorMessage.value = null;
    try {
      status.value = await client.stopDtsLive();
      syncRefreshTimer();
    } catch (error) {
      errorMessage.value = readableError(error);
    } finally {
      action.value = null;
    }
  };

  const launchExplorer = async (view: DtsLiveServiceStatus) => {
    if (
      !view.explorer_executable ||
      !view.launch_url ||
      !view.connector_session_id
    ) {
      throw new Error("实时种植会话缺少 Explorer 启动信息");
    }
    if (!bridge.capabilities.launchDtsExplorer) {
      throw new Error("请在 VerdFlow 桌面应用中启动 DTS 实时种植");
    }
    const result = await bridge.launchDtsExplorer({
      executablePath: view.explorer_executable,
      launchUrl: view.launch_url,
      sessionId: view.connector_session_id,
    });
    if (!result.ok) throw new Error(result.message);
  };

  const syncRefreshTimer = () => {
    if (running.value && refreshTimer === undefined) {
      refreshTimer = window.setInterval(() => void refresh(false), 1500);
    } else if (!running.value && refreshTimer !== undefined) {
      window.clearInterval(refreshTimer);
      refreshTimer = undefined;
    }
  };

  const dispose = () => {
    if (refreshTimer !== undefined) window.clearInterval(refreshTimer);
    refreshTimer = undefined;
  };

  return {
    action,
    dispose,
    errorMessage,
    initialize,
    managementAvailable,
    refresh,
    relaunch,
    running,
    start,
    status,
    stop,
  };
};

const readableError = (error: unknown) => {
  if (error instanceof LocalApiError) {
    if (error.code === "connection_failed") return "无法连接 VerdFlow 本地服务";
    if (error.code === "management_auth_required") {
      return "当前桌面应用没有实时种植服务管理权限";
    }
    return error.message;
  }
  return error instanceof Error ? error.message : "实时种植服务操作失败";
};
