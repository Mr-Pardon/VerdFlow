import { computed, onScopeDispose, ref, shallowRef } from "vue";
import { LocalApiClient } from "../../api";
import { platformBridge } from "../../platform";
import type { StarterManifest, StarterImportOperation } from "../../api/starterResources";

export function useStarterResources(client = new LocalApiClient(platformBridge())) {
  const manifest = shallowRef<StarterManifest>();
  const operation = shallowRef<StarterImportOperation>();
  const loading = ref(false);
  const submitting = ref(false);
  const error = ref("");
  const running = computed(() => submitting.value || operation.value?.status === "running" || operation.value?.status === "cancelling");
  let disposed = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let reading = false;
  const failure = (reason: unknown) => reason instanceof Error ? reason.message : "操作未完成，请重试。";
  const load = async () => {
    if (loading.value || running.value) return;
    loading.value = true;
    error.value = "";
    manifest.value = undefined;
    operation.value = undefined;
    try { const result = await client.starterResources(); if (!disposed) manifest.value = result; }
    catch (reason) { if (!disposed) error.value = failure(reason); }
    finally { if (!disposed) loading.value = false; }
  };
  const poll = async () => {
    if (disposed || reading || !operation.value) return;
    clearTimeout(timer);
    reading = true;
    try {
      const result = await client.starterImport(operation.value.operation_id);
      if (!disposed) {
        operation.value = result;
        error.value = "";
        if (running.value) timer = setTimeout(() => void poll(), 500);
      }
    } catch (reason) { if (!disposed) error.value = failure(reason); }
    finally { reading = false; }
  };
  const start = async () => {
    if (!manifest.value || running.value) return;
    submitting.value = true;
    error.value = "";
    try {
      const result = await client.importStarterResources(manifest.value.revision);
      if (disposed) { await client.cancelStarterImport(result.operation_id); return; }
      operation.value = result;
      timer = setTimeout(() => void poll(), 0);
    } catch (reason) { if (!disposed) error.value = failure(reason); }
    finally { if (!disposed) submitting.value = false; }
  };
  const cancel = async () => {
    if (!operation.value || !running.value) return;
    try {
      const result = await client.cancelStarterImport(operation.value.operation_id);
      if (!disposed) { operation.value = result; if (running.value) await poll(); }
    } catch (reason) { if (!disposed) error.value = failure(reason); }
  };
  onScopeDispose(() => {
    disposed = true;
    clearTimeout(timer);
    if (running.value && operation.value) void client.cancelStarterImport(operation.value.operation_id).catch(() => {});
  });
  return { manifest, operation, loading, submitting, error, running, load, start, cancel, poll };
}
