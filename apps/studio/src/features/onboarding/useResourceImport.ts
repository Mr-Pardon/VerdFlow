import { computed, onScopeDispose, ref, shallowRef } from "vue";
import { LocalApiClient } from "../../api";
import { platformBridge } from "../../platform";
import type { StarterImportOperation } from "../../api/starterResources";

export function useResourceImport(paths: readonly string[], client = new LocalApiClient(platformBridge())) {
  const operation = shallowRef<StarterImportOperation>();
  const error = ref("");
  const submitting = ref(false);
  const optionalIds = ref<string[]>([]);
  const overwriteIds = ref<string[]>([]);
  const busy = computed(() => submitting.value || ["preparing", "running", "cancelling"].includes(operation.value?.status ?? ""));
  const waiting = computed(() => operation.value?.status === "awaiting_confirmation");
  const finished = computed(() => !!operation.value && !busy.value && !waiting.value);
  let disposed = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let reading = false;
  let localStrategies: readonly string[] = [];
  const message = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);
  const accept = async (result: StarterImportOperation) => {
    if (disposed) { await client.cancelStarterImport(result.operation_id); return; }
    operation.value = result;
    error.value = result.error ?? "";
    if (["preparing", "running", "cancelling"].includes(result.status)) timer = setTimeout(() => void poll(), 400);
  };
  const poll = async () => {
    if (disposed || reading || !operation.value) return;
    clearTimeout(timer);
    reading = true;
    try { await accept(await client.starterImport(operation.value.operation_id)); }
    catch (reason) { if (!disposed) error.value = message(reason); }
    finally { reading = false; }
  };
  const prepare = async (strategies: readonly string[] = localStrategies) => {
    if (busy.value || disposed) return;
    submitting.value = true;
    error.value = "";
    clearTimeout(timer);
    try {
      if (waiting.value && operation.value) await client.cancelStarterImport(operation.value.operation_id);
      localStrategies = strategies;
      optionalIds.value = [];
      overwriteIds.value = [];
      await accept(await client.prepareResourceImport(paths, paths.length === 0, strategies));
    } catch (reason) { if (!disposed) error.value = message(reason); }
    finally { if (!disposed) submitting.value = false; }
  };
  const confirm = async () => {
    if (!waiting.value || !operation.value || busy.value) return;
    submitting.value = true;
    error.value = "";
    try { await accept(await client.confirmResourceImport(operation.value.operation_id, optionalIds.value, overwriteIds.value)); }
    catch (reason) { if (!disposed) error.value = message(reason); }
    finally { if (!disposed) submitting.value = false; }
  };
  const cancel = async () => {
    if (!operation.value) return;
    clearTimeout(timer);
    try { await accept(await client.cancelStarterImport(operation.value.operation_id)); }
    catch (reason) { if (!disposed) error.value = message(reason); }
  };
  onScopeDispose(() => {
    disposed = true;
    clearTimeout(timer);
    if (operation.value && !finished.value) void client.cancelStarterImport(operation.value.operation_id).catch(() => {});
  });
  return { operation, error, submitting, busy, waiting, finished, optionalIds, overwriteIds, prepare, confirm, cancel, poll };
}
