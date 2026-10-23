import { ref, watch } from "vue";
import { LocalApiClient } from "../../api";
import type { PlantRunExecutionInfo } from "../../api";
import { platformBridge } from "../../platform";

export const useOutputAssetSelection = (
  sessionId: () => string | null | undefined,
  execution: () => PlantRunExecutionInfo | null,
) => {
  const selected = ref<string[] | null>(null);
  const loading = ref(false);
  const error = ref<string | null>(null);
  let revision = 0;
  const storageKey = () => `verdflow.output-assets.${execution()?.generated_point_set_id ?? ""}`;
  watch(() => execution()?.execution_id, async () => {
    const epoch = ++revision;
    selected.value = null;
    error.value = null;
    loading.value = true;
    try {
      const raw = localStorage.getItem(storageKey());
      if (raw !== null) {
        let value: unknown;
        try { value = JSON.parse(raw); } catch { localStorage.removeItem(storageKey()); }
        if (value === null || (Array.isArray(value) && value.every((item) => typeof item === "string"))) {
          selected.value = value;
          return;
        }
      }
      const current = execution();
      const session = sessionId();
      if (current?.status === "succeeded" && session) {
        const info = await new LocalApiClient(platformBridge()).plantPreviewExport(session, current.execution_id);
        if (epoch === revision) selected.value = info.selected_asset_ids ?? null;
      }
    } catch (cause) {
      if (epoch === revision) error.value = cause instanceof Error ? cause.message : "输出选择读取失败";
    } finally {
      if (epoch === revision) loading.value = false;
    }
  }, { immediate: true });
  const update = (value: string[] | null) => {
    error.value = null;
    selected.value = value === null ? null : [...value].sort();
    localStorage.setItem(storageKey(), JSON.stringify(selected.value));
  };
  return { selected, loading, error, update };
};
