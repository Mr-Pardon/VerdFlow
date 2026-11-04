import { onScopeDispose, ref, shallowRef } from "vue";
import { LocalApiClient } from "../../api";
import type { StrategyUpdate } from "../../api/resourceCenter";
import { platformBridge } from "../../platform";

export function useStrategyUpdates(client = new LocalApiClient(platformBridge())) {
  const items = shallowRef<StrategyUpdate[]>([]);
  const checking = ref(false);
  const warning = ref("");
  let disposed = false;
  const check = async (refresh = false) => {
    if (checking.value) return;
    checking.value = true;
    warning.value = "";
    try {
      const result = await client.strategyResourceUpdates(refresh);
      if (!disposed) { items.value = result.items; warning.value = result.warning; }
    } catch { if (!disposed) warning.value = "暂时无法检查策略更新，本地策略仍可使用。"; }
    finally { if (!disposed) checking.value = false; }
  };
  onScopeDispose(() => { disposed = true; });
  return { items, checking, warning, check };
}
