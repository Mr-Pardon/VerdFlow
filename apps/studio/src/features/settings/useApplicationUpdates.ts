import { computed, onMounted, onUnmounted, ref, type InjectionKey } from "vue";
import { platformBridge, type ApplicationUpdateApi, type ApplicationUpdatePreferences, type ApplicationUpdateState } from "../../platform";

export const useApplicationUpdates = (api: ApplicationUpdateApi | undefined = platformBridge().applicationUpdates) => {
  const state = ref<ApplicationUpdateState | null>(null);
  const error = ref<string | null>(null);
  const acting = ref(false);
  const savingPreferences = ref(false);
  let unsubscribe: (() => void) | undefined;
  let disposed = false;
  const receive = (value: ApplicationUpdateState) => { if (!disposed) state.value = value; };

  onMounted(() => {
    if (!api) return;
    unsubscribe = api.subscribe(receive);
    void api.status().then(result => { if (result.ok) receive(result.value); }).catch(() => undefined);
  });
  onUnmounted(() => { disposed = true; unsubscribe?.(); });

  const run = async (command: "check" | "download" | "cancel" | "apply") => {
    if (!api || (acting.value && command !== "cancel")) return;
    error.value = null;
    acting.value = true;
    try {
      const result = await api[command]();
      if (disposed) return;
      if (!result.ok) error.value = result.message;
      else if (result.value) receive(result.value);
    } catch { if (!disposed) error.value = "更新操作未完成，请稍后重试"; }
    finally { if (!disposed) acting.value = false; }
  };
  const preferences = async (value: ApplicationUpdatePreferences) => {
    if (!api || savingPreferences.value) return;
    savingPreferences.value = true;
    try {
      const result = await api.preferences(value);
      if (disposed) return;
      if (result.ok) receive(result.value);
      else error.value = result.message;
    } catch { if (!disposed) error.value = "更新偏好未保存"; }
    finally { if (!disposed) savingPreferences.value = false; }
  };
  const label = computed(() => {
    const value = state.value;
    if (!value?.supported) return "";
    if (!value.configured) return "更新源尚未配置";
    if (value.stage === "checking") return "正在检查更新";
    if (value.stage === "downloading") return `正在下载更新 ${value.progress}%`;
    if (value.stage === "preparing") return "正在校验并准备更新";
    if (value.stage === "ready") return `v${value.latestVersion} 已就绪，可重启更新`;
    if (value.stage === "applying") return "正在准备重启更新";
    if (value.stage === "available") return `发现新版本 v${value.latestVersion}`;
    if (value.stage === "error") return "暂时无法检查更新";
    if (value.stage === "idle" && value.message) return value.message;
    return value.lastCheckedAt ? "当前已是最新版本" : "尚未检查更新";
  });
  const attention = computed(() => state.value && ["available", "downloading", "preparing", "ready", "applying"].includes(state.value.stage));
  return { state, error, acting, savingPreferences, run, preferences, label, attention };
};

export type ApplicationUpdates = ReturnType<typeof useApplicationUpdates>;
export const applicationUpdatesKey: InjectionKey<ApplicationUpdates> = Symbol("application-updates");
