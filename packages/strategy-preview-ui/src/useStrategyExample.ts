import { onScopeDispose, ref, shallowRef, watch } from "vue";

import type {
  StrategyPreviewInput,
  StrategyPreviewResult,
  StrategyPreviewClient,
} from "./types";

export interface StrategyExampleCache {
  signature: string;
  result: StrategyPreviewResult | null;
}

export function useStrategyExample(
  input: () => StrategyPreviewInput | null,
  cache: StrategyExampleCache | undefined,
  client: StrategyPreviewClient,
) {
  const result = shallowRef<StrategyPreviewResult | null>(null);
  const updating = ref(false);
  const error = ref("");
  let revision = 0;
  let disposed = false;
  let activeId: string | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const cancel = (id: string) => {
    void client.cancelStrategyExample(id).catch(() => {});
  };
  const clear = () => {
    if (timer !== null) clearTimeout(timer);
    timer = null;
    if (activeId) cancel(activeId);
    activeId = null;
  };

  const refresh = () => {
    const current = ++revision;
    clear();
    error.value = "";
    const request = input();
    if (!request) {
      updating.value = false;
      return;
    }
    if (result.value?.package_hash !== request.package_hash)
      result.value = null;
    const signature = JSON.stringify(request);
    if (cache?.signature === signature && cache.result) {
      result.value = cache.result;
      updating.value = false;
      return;
    }
    updating.value = true;
    const latest = () => !disposed && current === revision;
    const fail = (cause: unknown) => {
      if (!latest()) return;
      updating.value = false;
      error.value = cause instanceof Error ? cause.message : "示例暂未更新";
      if (activeId) cancel(activeId);
      activeId = null;
    };
    const poll = async (id: string) => {
      try {
        const operation = await client.strategyExample(id);
        if (!latest()) return;
        if (operation.status === "succeeded" && operation.result) {
          if (operation.result.package_hash !== request.package_hash)
            throw new Error("示例与当前策略不一致");
          result.value = operation.result;
          if (cache) {
            cache.signature = signature;
            cache.result = operation.result;
          }
          updating.value = false;
        } else if (
          operation.status === "failed" ||
          operation.status === "cancelled"
        ) {
          throw new Error(operation.error_message || "示例暂未更新");
        } else {
          timer = setTimeout(() => {
            if (latest()) void poll(id);
          }, 120);
        }
      } catch (cause) {
        fail(cause);
      }
    };
    timer = setTimeout(async () => {
      try {
        const operation = await client.submitStrategyExample(request);
        if (!latest()) {
          cancel(operation.preview_id);
          return;
        }
        activeId = operation.preview_id;
        await poll(operation.preview_id);
      } catch (cause) {
        fail(cause);
      }
    }, 140);
  };

  const stop = watch(() => JSON.stringify(input()), refresh, {
    immediate: true,
  });
  onScopeDispose(() => {
    disposed = true;
    revision += 1;
    stop();
    clear();
  });
  return { result, updating, error, refresh };
}
