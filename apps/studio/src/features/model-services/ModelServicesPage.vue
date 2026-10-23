<script setup lang="ts">
import {
  Bot,
  CheckCircle2,
  KeyRound,
  LoaderCircle,
  Plus,
  RefreshCw,
  Server,
  Settings2,
  Trash2,
  Unplug,
  X,
  XCircle,
  Zap,
} from "@lucide/vue";
import { computed, onMounted, reactive, ref } from "vue";

import type {
  ModelProviderInfo,
  ModelProviderProtocol,
  ModelProviderReasoningMode,
  SaveModelProviderInput,
} from "../../api";
import { confirmAction } from "../../components/confirmDialog";
import { useModelServices } from "./useModelServices";
import { useGuidePage } from "../onboarding/context";

interface ProviderDraft {
  providerId: string;
  displayName: string;
  protocol: ModelProviderProtocol;
  endpoint: string;
  modelDiscoveryEndpoint: string;
  defaultModel: string;
  contextWindowTokens: string;
  maxOutputTokens: string;
  defaultMaxOutputTokens: string;
  reasoningMode: ModelProviderReasoningMode;
  apiKey: string;
}

const services = useModelServices();
useGuidePage("models", () => ({
  ready: services.providers.value.some(item => item.secret_configured && item.default_model),
  status: services.providers.value.some(item => item.secret_configured && item.default_model) ? "已有密钥与模型配置；可在页面测试连接。" : "尚未配置模型服务，可以跳过。",
  loading: services.loading.value, busy: services.action.value !== null || dialogOpen.value, error: services.errorMessage.value,
}));
const editingProvider = ref<ModelProviderInfo | null>(null);
const dialogOpen = ref(false);
const formError = ref<string | null>(null);
const draft = reactive<ProviderDraft>(emptyDraft());

const isBuiltin = computed(() => editingProvider.value?.builtin === true);
const dialogBusy = computed(() => services.action.value !== null);
const availableModels = computed(() =>
  editingProvider.value
    ? services.modelOptions.value[editingProvider.value.provider_id] ?? []
    : [],
);
const canDiscover = computed(() =>
  Boolean(
    editingProvider.value?.model_discovery_supported &&
      (editingProvider.value.secret_configured || draft.apiKey.trim()),
  ),
);
const dialogTitle = computed(() =>
  editingProvider.value
    ? `配置 ${editingProvider.value.display_name}`
    : "添加自定义服务",
);

const protocols: { value: ModelProviderProtocol; label: string }[] = [
  { value: "openai-responses", label: "OpenAI Responses" },
  { value: "anthropic-messages", label: "Anthropic Messages" },
  { value: "openai-chat-completions", label: "OpenAI Chat Completions" },
];

onMounted(() => void services.initialize());

const openCreate = () => {
  editingProvider.value = null;
  Object.assign(draft, emptyDraft());
  formError.value = null;
  dialogOpen.value = true;
};

const openConfigure = (provider: ModelProviderInfo) => {
  editingProvider.value = provider;
  Object.assign(draft, {
    providerId: provider.provider_id,
    displayName: provider.display_name,
    protocol: provider.protocol as ModelProviderProtocol,
    endpoint: provider.endpoint,
    modelDiscoveryEndpoint: provider.model_discovery_endpoint ?? "",
    defaultModel: provider.default_model ?? "",
    contextWindowTokens: optionalNumber(provider.context_window_tokens),
    maxOutputTokens: optionalNumber(provider.max_output_tokens),
    defaultMaxOutputTokens: String(provider.default_max_output_tokens),
    reasoningMode: provider.reasoning_mode as ModelProviderReasoningMode,
    apiKey: "",
  });
  formError.value = null;
  dialogOpen.value = true;
};

const closeDialog = () => {
  if (dialogBusy.value) return;
  dialogOpen.value = false;
  editingProvider.value = null;
  formError.value = null;
};

const save = async () => {
  formError.value = validateDraft();
  if (formError.value) return;

  if (isBuiltin.value) {
    const updated = await services.saveBuiltinConfiguration(
      draft.providerId,
      draft.defaultModel.trim(),
      draft.apiKey,
    );
    if (updated) closeDialog();
    return;
  }

  const input: SaveModelProviderInput = {
    display_name: draft.displayName.trim(),
    protocol: draft.protocol,
    endpoint: draft.endpoint.trim(),
    model_discovery_endpoint: draft.modelDiscoveryEndpoint.trim() || null,
    default_model: draft.defaultModel.trim(),
    capabilities: {
      context_window_tokens: nullableInteger(draft.contextWindowTokens),
      max_output_tokens: nullableInteger(draft.maxOutputTokens),
      default_max_output_tokens: Number(draft.defaultMaxOutputTokens),
      reasoning_mode:
        draft.protocol === "openai-chat-completions"
          ? draft.reasoningMode
          : "provider_default",
    },
  };
  const updated = await services.saveProvider(
    draft.providerId.trim(),
    input,
    draft.apiKey,
  );
  if (updated) closeDialog();
};

const removeSecret = async (provider: ModelProviderInfo) => {
  const confirmed = await confirmAction({
    title: "删除 API Key？",
    message: `删除“${provider.display_name}”的 API Key 后，该服务将不能执行模型任务。`,
    confirmLabel: "删除 Key",
    tone: "danger",
  });
  if (!confirmed) return;
  const removed = await services.deleteSecret(provider.provider_id);
  if (removed && editingProvider.value?.provider_id === provider.provider_id) {
    closeDialog();
  }
};

const removeProvider = async (provider: ModelProviderInfo) => {
  const confirmed = await confirmAction({
    title: "删除模型服务？",
    message: `将删除自定义服务“${provider.display_name}”。已完成任务的运行记录不会被删除，API Key 也不会随配置删除。`,
    confirmLabel: "删除服务",
    tone: "danger",
  });
  if (!confirmed) return;
  await services.deleteProvider(provider.provider_id);
};

const validateDraft = (): string | null => {
  if (!draft.defaultModel.trim()) return "请选择或填写模型";
  if (isBuiltin.value) {
    if (!editingProvider.value?.secret_configured && !draft.apiKey.trim()) {
      return "请先填写 API Key";
    }
    return null;
  }
  if (!/^[a-z0-9][a-z0-9._-]{0,63}$/.test(draft.providerId.trim())) {
    return "服务 ID 需使用小写字母、数字、点、下划线或连字符";
  }
  if (!draft.displayName.trim()) return "请填写服务名称";
  if (!draft.endpoint.trim()) return "请填写 API 端点";
  for (const [label, value, nullable] of [
    ["上下文窗口", draft.contextWindowTokens, true],
    ["最大输出", draft.maxOutputTokens, true],
    ["默认输出", draft.defaultMaxOutputTokens, false],
  ] as const) {
    if (nullable && !value.trim()) continue;
    const parsed = Number(value);
    if (!Number.isInteger(parsed) || parsed < 256) {
      return `${label}必须是大于等于 256 的整数`;
    }
  }
  const maximum = nullableInteger(draft.maxOutputTokens);
  const defaultMaximum = Number(draft.defaultMaxOutputTokens);
  if (maximum !== null && defaultMaximum > maximum) {
    return "默认输出不能大于最大输出";
  }
  const context = nullableInteger(draft.contextWindowTokens);
  if (context !== null && maximum !== null && maximum > context) {
    return "最大输出不能大于上下文窗口";
  }
  return null;
};

const actionIs = (providerId: string, kind: string) =>
  services.action.value?.providerId === providerId &&
  services.action.value.kind === kind;

const protocolLabel = (protocol: string) =>
  protocols.find((item) => item.value === protocol)?.label ?? protocol;

const nullableInteger = (value: string) =>
  value.trim() ? Number(value) : null;

const optionalNumber = (value: number | null) =>
  value === null ? "" : String(value);

function emptyDraft(): ProviderDraft {
  return {
    providerId: "",
    displayName: "",
    protocol: "openai-chat-completions",
    endpoint: "",
    modelDiscoveryEndpoint: "",
    defaultModel: "",
    contextWindowTokens: "",
    maxOutputTokens: "",
    defaultMaxOutputTokens: "8192",
    reasoningMode: "provider_default",
    apiKey: "",
  };
}

const discoverModels = async () => {
  const provider = editingProvider.value;
  if (!provider) return;
  const models = await services.discoverModels(provider.provider_id, draft.apiKey);
  if (!models) return;
  draft.apiKey = "";
  editingProvider.value =
    services.providers.value.find(
      (item) => item.provider_id === provider.provider_id,
    ) ?? provider;
};
</script>

<template>
  <section class="model-services-page">
    <header class="page-header model-services-header">
      <div>
        <span class="page-header__eyebrow">运行</span>
        <h1>模型服务</h1>
        <p>管理材质分析使用的 API Provider 与本机凭据。</p>
      </div>
      <div class="page-actions">
        <button class="icon-button" type="button" title="刷新" @click="services.refresh">
          <RefreshCw :size="17" :class="{ spinning: services.loading.value }" />
        </button>
        <button
          class="primary-button"
          type="button"
          :disabled="!services.managementAvailable.value || services.action.value !== null"
          @click="openCreate"
        >
          <Plus :size="17" />
          添加自定义服务
        </button>
      </div>
    </header>

    <div v-if="services.errorMessage.value" class="inline-alert model-services-alert" role="alert">
      <div><strong>模型服务操作未完成</strong><p>{{ services.errorMessage.value }}</p></div>
      <button type="button" @click="services.refresh">重新读取</button>
    </div>
    <div v-else-if="services.notice.value" class="model-services-notice" role="status">
      <CheckCircle2 :size="16" />{{ services.notice.value }}
    </div>

    <div class="model-service-summary" aria-label="模型服务概览">
      <span><small>API Provider</small><strong>{{ services.providers.value.length }}</strong></span>
      <span><small>已配置凭据</small><strong>{{ services.configuredCount.value }}</strong></span>
      <span><small>内置模板</small><strong>{{ services.builtinProviders.value.length }}</strong></span>
      <span><small>自定义服务</small><strong>{{ services.customProviders.value.length }}</strong></span>
    </div>

    <main class="model-service-content">
      <div v-if="services.loading.value && !services.providers.value.length" class="model-service-loading">
        <LoaderCircle :size="19" class="spinning" />正在读取模型服务
      </div>

      <template v-else>
        <section class="provider-section">
          <header>
            <div><h2>内置模板</h2><p>预设协议与端点，API Key 和实际模型由本机选择。</p></div>
            <strong>{{ services.builtinProviders.value.length }}</strong>
          </header>
          <div class="provider-table">
            <article v-for="provider in services.builtinProviders.value" :key="provider.provider_id" class="provider-row">
              <div class="provider-identity">
                <span class="provider-mark"><Server :size="18" /></span>
                <span><strong>{{ provider.display_name }}</strong><small>{{ provider.provider_id }} · 内置</small></span>
              </div>
              <div class="provider-field"><small>协议</small><strong>{{ protocolLabel(provider.protocol) }}</strong></div>
              <div class="provider-field"><small>当前模型</small><strong>{{ provider.default_model || '尚未选择' }}</strong></div>
              <div class="provider-state" :data-ready="provider.secret_configured && Boolean(provider.default_model)">
                <span class="status-dot" :data-tone="provider.secret_configured && provider.default_model ? 'success' : 'warning'" />
                {{ provider.secret_configured && provider.default_model ? '配置完成' : '等待配置' }}
              </div>
              <div class="provider-actions">
                <button
                  class="secondary-button"
                  type="button"
                  :disabled="!services.managementAvailable.value || services.action.value !== null"
                  @click="openConfigure(provider)"
                ><Settings2 :size="16" />配置</button>
                <button
                  class="icon-button"
                  type="button"
                  title="测试连接"
                  :disabled="!services.managementAvailable.value || !provider.secret_configured || !provider.default_model || services.action.value !== null"
                  @click="services.testProvider(provider.provider_id)"
                >
                  <LoaderCircle v-if="actionIs(provider.provider_id, 'test')" :size="16" class="spinning" />
                  <Zap v-else :size="16" />
                </button>
              </div>
              <div v-if="services.testResults.value[provider.provider_id]" class="provider-test-result" :data-success="services.testResults.value[provider.provider_id]?.succeeded">
                <CheckCircle2 v-if="services.testResults.value[provider.provider_id]?.succeeded" :size="15" />
                <XCircle v-else :size="15" />
                <span>{{ services.testResults.value[provider.provider_id]?.succeeded ? '连接成功' : '连接失败' }}</span>
                <small>{{ services.testResults.value[provider.provider_id]?.duration_ms }} ms · {{ services.testResults.value[provider.provider_id]?.message }}</small>
              </div>
            </article>
          </div>
        </section>

        <section class="provider-section">
          <header>
            <div><h2>自定义服务</h2><p>接入兼容受支持协议的团队网关或模型平台。</p></div>
            <strong>{{ services.customProviders.value.length }}</strong>
          </header>
          <div v-if="services.customProviders.value.length" class="provider-table">
            <article v-for="provider in services.customProviders.value" :key="provider.provider_id" class="provider-row">
              <div class="provider-identity">
                <span class="provider-mark"><Server :size="18" /></span>
                <span><strong>{{ provider.display_name }}</strong><small>{{ provider.provider_id }} · 自定义</small></span>
              </div>
              <div class="provider-field"><small>协议</small><strong>{{ protocolLabel(provider.protocol) }}</strong></div>
              <div class="provider-field"><small>当前模型</small><strong>{{ provider.default_model || '尚未选择' }}</strong></div>
              <div class="provider-state" :data-ready="provider.secret_configured">
                <span class="status-dot" :data-tone="provider.secret_configured ? 'success' : 'warning'" />
                {{ provider.secret_configured ? '凭据已配置' : '等待配置' }}
              </div>
              <div class="provider-actions">
                <button class="secondary-button" type="button" :disabled="!services.managementAvailable.value || services.action.value !== null" @click="openConfigure(provider)">
                  <Settings2 :size="16" />配置
                </button>
                <button class="icon-button" type="button" title="测试连接" :disabled="!services.managementAvailable.value || !provider.secret_configured || !provider.default_model || services.action.value !== null" @click="services.testProvider(provider.provider_id)">
                  <LoaderCircle v-if="actionIs(provider.provider_id, 'test')" :size="16" class="spinning" />
                  <Zap v-else :size="16" />
                </button>
                <button class="icon-button danger" type="button" title="删除自定义服务" :disabled="!services.managementAvailable.value || services.action.value !== null" @click="removeProvider(provider)">
                  <Trash2 :size="16" />
                </button>
              </div>
              <div v-if="services.testResults.value[provider.provider_id]" class="provider-test-result" :data-success="services.testResults.value[provider.provider_id]?.succeeded">
                <CheckCircle2 v-if="services.testResults.value[provider.provider_id]?.succeeded" :size="15" />
                <XCircle v-else :size="15" />
                <span>{{ services.testResults.value[provider.provider_id]?.succeeded ? '连接成功' : '连接失败' }}</span>
                <small>{{ services.testResults.value[provider.provider_id]?.duration_ms }} ms · {{ services.testResults.value[provider.provider_id]?.message }}</small>
              </div>
            </article>
          </div>
          <button v-else class="provider-empty" type="button" :disabled="!services.managementAvailable.value" @click="openCreate">
            <Plus :size="18" /><span><strong>添加第一个自定义服务</strong><small>支持 OpenAI Responses、Anthropic Messages 与 OpenAI Chat Completions。</small></span>
          </button>
        </section>

        <section class="agent-provider-strip">
          <span><Bot :size="19" /></span>
          <div><h2>Agent Provider</h2><p>订阅额度与有状态 Agent 执行将在后续阶段接入。</p></div>
          <em>规划中</em>
        </section>
      </template>
    </main>

    <div v-if="dialogOpen" class="studio-modal-backdrop" @mousedown.self="closeDialog">
      <form class="studio-modal model-provider-modal" role="dialog" aria-modal="true" @submit.prevent="save">
        <header>
          <div><span>{{ isBuiltin ? '内置 API Provider' : editingProvider ? '自定义 API Provider' : '新建 API Provider' }}</span><h3>{{ dialogTitle }}</h3></div>
          <button type="button" title="关闭" :disabled="dialogBusy" @click="closeDialog"><X :size="18" /></button>
        </header>

        <div class="model-provider-modal__body">
          <div v-if="isBuiltin" class="provider-template-summary">
            <span class="provider-mark"><Server :size="19" /></span>
            <div><strong>{{ editingProvider?.display_name }}</strong><small>{{ protocolLabel(editingProvider?.protocol ?? '') }}</small><code>{{ editingProvider?.endpoint }}</code></div>
            <em>模板参数只读</em>
          </div>

          <template v-else>
            <div class="model-provider-form-grid">
              <label class="form-field"><span>服务 ID <em>必填</em></span><input v-model="draft.providerId" :readonly="Boolean(editingProvider)" autocomplete="off" placeholder="例如 team-gateway" /></label>
              <label class="form-field"><span>服务名称 <em>必填</em></span><input v-model="draft.displayName" autocomplete="off" placeholder="例如 Team Gateway" /></label>
            </div>
            <label class="form-field"><span>协议 <em>必填</em></span><select v-model="draft.protocol"><option v-for="protocol in protocols" :key="protocol.value" :value="protocol.value">{{ protocol.label }}</option></select></label>
            <label class="form-field"><span>API 端点 <em>必填</em></span><input v-model="draft.endpoint" autocomplete="off" placeholder="https://models.example.com/v1/chat/completions" /></label>
            <label class="form-field"><span>模型发现端点</span><input v-model="draft.modelDiscoveryEndpoint" autocomplete="off" placeholder="可选，例如 https://models.example.com/v1/models" /></label>

            <fieldset class="provider-capabilities">
              <legend>能力限制</legend>
              <div class="model-provider-form-grid model-provider-form-grid--three">
                <label class="form-field"><span>上下文窗口</span><input v-model="draft.contextWindowTokens" inputmode="numeric" placeholder="不限制" /></label>
                <label class="form-field"><span>最大输出</span><input v-model="draft.maxOutputTokens" inputmode="numeric" placeholder="不限制" /></label>
                <label class="form-field"><span>默认输出</span><input v-model="draft.defaultMaxOutputTokens" inputmode="numeric" /></label>
              </div>
              <label class="form-field"><span>推理模式</span><select v-model="draft.reasoningMode" :disabled="draft.protocol !== 'openai-chat-completions'"><option value="provider_default">跟随 Provider</option><option value="disabled">关闭</option><option value="enabled">开启</option></select></label>
            </fieldset>
          </template>

          <label class="form-field provider-secret-field">
            <span>API Key {{ editingProvider?.secret_configured ? '（已配置）' : '' }}</span>
            <div><KeyRound :size="16" /><input v-model="draft.apiKey" type="password" autocomplete="new-password" :placeholder="editingProvider?.secret_configured ? '留空则保留现有密钥' : '输入 API Key'" /></div>
            <small>密钥写入本机 SecretStore，Studio 不会读取或回显原值。</small>
          </label>

          <label class="form-field provider-model-field">
            <span>使用模型 <em>必填</em></span>
            <div class="provider-model-picker">
              <input
                v-model="draft.defaultModel"
                :list="editingProvider ? `provider-models-${editingProvider.provider_id}` : undefined"
                autocomplete="off"
                placeholder="选择列表中的模型，或手动填写模型 ID"
              />
              <button
                v-if="editingProvider?.model_discovery_supported"
                class="secondary-button"
                type="button"
                :disabled="dialogBusy || !canDiscover"
                @click="discoverModels"
              >
                <LoaderCircle v-if="actionIs(editingProvider.provider_id, 'discover')" :size="16" class="spinning" />
                <RefreshCw v-else :size="16" />
                {{ editingProvider.secret_configured ? '获取模型' : '保存密钥并获取' }}
              </button>
            </div>
            <datalist v-if="editingProvider" :id="`provider-models-${editingProvider.provider_id}`">
              <option v-for="model in availableModels" :key="model.model_id" :value="model.model_id">
                {{ model.display_name || model.owned_by || model.model_id }}
              </option>
            </datalist>
            <small>模型列表仅辅助选择；未提供列表或目标模型未列出时仍可手动填写。</small>
          </label>

          <div v-if="formError || services.errorMessage.value" class="dialog-error">{{ formError || services.errorMessage.value }}</div>

          <button v-if="editingProvider?.secret_configured" class="provider-remove-secret" type="button" :disabled="dialogBusy" @click="removeSecret(editingProvider)">
            <Unplug :size="16" />删除已配置密钥
          </button>
        </div>

        <footer>
          <span v-if="!services.managementAvailable.value">当前桌面应用没有模型服务管理权限</span>
          <button class="secondary-button" type="button" :disabled="dialogBusy" @click="closeDialog">取消</button>
          <button class="primary-button" type="submit" :disabled="!services.managementAvailable.value || dialogBusy">
            <LoaderCircle v-if="dialogBusy" :size="16" class="spinning" />
            <Settings2 v-else-if="isBuiltin" :size="16" />
            <Settings2 v-else :size="16" />
            保存配置
          </button>
        </footer>
      </form>
    </div>
  </section>
</template>
