import { computed, ref, shallowRef } from "vue";

import { LocalApiClient, LocalApiError } from "../../api";
import type {
  ModelProviderInfo,
  ModelProviderModelInfo,
  ModelProviderTestResult,
  SaveModelProviderInput,
} from "../../api";
import { platformBridge } from "../../platform";

export type ModelServiceAction =
  | "save"
  | "secret"
  | "model"
  | "discover"
  | "delete-secret"
  | "delete"
  | "test";

export const useModelServices = () => {
  const bridge = platformBridge();
  const client = new LocalApiClient(bridge);
  const providers = shallowRef<ModelProviderInfo[]>([]);
  const loading = ref(true);
  const managementAvailable = ref(false);
  const action = ref<{ providerId: string; kind: ModelServiceAction } | null>(
    null,
  );
  const errorMessage = ref<string | null>(null);
  const notice = ref<string | null>(null);
  const testResults = ref<Record<string, ModelProviderTestResult>>({});
  const modelOptions = ref<Record<string, ModelProviderModelInfo[]>>({});

  const builtinProviders = computed(() =>
    providers.value.filter((provider) => provider.builtin),
  );
  const customProviders = computed(() =>
    providers.value.filter((provider) => !provider.builtin),
  );
  const configuredCount = computed(
    () => providers.value.filter((provider) => provider.secret_configured).length,
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
      const result = await client.modelProviders();
      providers.value = result.items;
    } catch (error) {
      errorMessage.value = readableError(error);
    } finally {
      loading.value = false;
    }
  };

  const saveProvider = async (
    providerId: string,
    input: SaveModelProviderInput,
    apiKey: string,
  ) => {
    action.value = { providerId, kind: "save" };
    errorMessage.value = null;
    notice.value = null;
    try {
      let updated = await client.saveModelProvider(providerId, input);
      updateProvider(updated);
      if (apiKey.trim()) {
        action.value = { providerId, kind: "secret" };
        updated = await client.setModelProviderSecret(providerId, apiKey.trim());
        updateProvider(updated);
      }
      notice.value = `已保存 ${updated.display_name}`;
      return updated;
    } catch (error) {
      errorMessage.value = readableError(error);
      return null;
    } finally {
      action.value = null;
    }
  };

  const saveBuiltinConfiguration = async (
    providerId: string,
    model: string,
    apiKey: string,
  ) => {
    action.value = { providerId, kind: "model" };
    errorMessage.value = null;
    notice.value = null;
    try {
      let updated: ModelProviderInfo | null = null;
      if (apiKey.trim()) {
        action.value = { providerId, kind: "secret" };
        updated = await client.setModelProviderSecret(providerId, apiKey.trim());
        updateProvider(updated);
      }
      action.value = { providerId, kind: "model" };
      updated = await client.setModelProviderModel(providerId, model.trim());
      updateProvider(updated);
      delete testResults.value[providerId];
      notice.value = `已保存 ${updated.display_name} 的模型配置`;
      return updated;
    } catch (error) {
      errorMessage.value = readableError(error);
      return null;
    } finally {
      action.value = null;
    }
  };

  const discoverModels = async (providerId: string, apiKey = "") => {
    action.value = { providerId, kind: "discover" };
    errorMessage.value = null;
    notice.value = null;
    try {
      if (apiKey.trim()) {
        const updated = await client.setModelProviderSecret(
          providerId,
          apiKey.trim(),
        );
        updateProvider(updated);
      }
      const result = await client.modelProviderModels(providerId);
      modelOptions.value = {
        ...modelOptions.value,
        [providerId]: result.items,
      };
      notice.value = `已读取 ${result.items.length} 个可用模型`;
      return result.items;
    } catch (error) {
      errorMessage.value = readableError(error);
      return null;
    } finally {
      action.value = null;
    }
  };

  const deleteSecret = async (providerId: string) => {
    action.value = { providerId, kind: "delete-secret" };
    errorMessage.value = null;
    notice.value = null;
    try {
      const updated = await client.deleteModelProviderSecret(providerId);
      updateProvider(updated);
      delete testResults.value[providerId];
      notice.value = `已删除 ${updated.display_name} 的密钥`;
      return true;
    } catch (error) {
      errorMessage.value = readableError(error);
      return false;
    } finally {
      action.value = null;
    }
  };

  const deleteProvider = async (providerId: string) => {
    action.value = { providerId, kind: "delete" };
    errorMessage.value = null;
    notice.value = null;
    try {
      const removed = await client.deleteModelProvider(providerId);
      providers.value = providers.value.filter(
        (provider) => provider.provider_id !== providerId,
      );
      delete testResults.value[providerId];
      notice.value = `已删除 ${removed.display_name}`;
      return true;
    } catch (error) {
      errorMessage.value = readableError(error);
      return false;
    } finally {
      action.value = null;
    }
  };

  const testProvider = async (providerId: string) => {
    action.value = { providerId, kind: "test" };
    errorMessage.value = null;
    notice.value = null;
    try {
      const result = await client.testModelProvider(providerId);
      testResults.value = { ...testResults.value, [providerId]: result };
      return result;
    } catch (error) {
      errorMessage.value = readableError(error);
      return null;
    } finally {
      action.value = null;
    }
  };

  const updateProvider = (updated: ModelProviderInfo) => {
    const exists = providers.value.some(
      (provider) => provider.provider_id === updated.provider_id,
    );
    providers.value = exists
      ? providers.value.map((provider) =>
          provider.provider_id === updated.provider_id ? updated : provider,
        )
      : [...providers.value, updated];
  };

  return {
    action,
    builtinProviders,
    configuredCount,
    customProviders,
    deleteProvider,
    deleteSecret,
    discoverModels,
    errorMessage,
    initialize,
    loading,
    managementAvailable,
    modelOptions,
    notice,
    providers,
    refresh,
    saveBuiltinConfiguration,
    saveProvider,
    testProvider,
    testResults,
  };
};

const readableError = (error: unknown) => {
  if (error instanceof LocalApiError) {
    if (error.code === "connection_failed") return "无法连接 VerdFlow 本地服务";
    if (error.code === "management_auth_required") {
      return "当前桌面应用没有模型服务管理权限";
    }
    return error.message;
  }
  return error instanceof Error ? error.message : "模型服务操作失败";
};
