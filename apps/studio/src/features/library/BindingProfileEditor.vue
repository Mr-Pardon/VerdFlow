<script setup lang="ts">
import {
  ArrowLeft,
  ArrowRight,
  Check,
  CircleCheck,
  LoaderCircle,
  Save,
  Sprout,
  X,
} from "@lucide/vue";
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef } from "vue";
import {
  RolePurposePanel,
  roleColor,
  roleGroups,
} from "@verdflow/role-guide-ui";

import PlanMappingWorkspace from "../../components/PlanMappingWorkspace.vue";
import StrategyParameterEditor from "../../components/StrategyParameterEditor.vue";
import { confirmAction } from "../../components/confirmDialog";
import {
  parameterPresetValues,
  parameterValuesValid,
  visibleParameters,
} from "../shared/parameterPresets";
import { LocalApiClient, LocalApiError } from "../../api";
import type {
  AssetCatalogAssetInfo,
  AssetCatalogCollectionInfo,
  AssetCatalogSummary,
  RoleBindingProfileDetail,
  StrategyLibraryDetail,
  StrategyLibrarySummary,
} from "../../api";
import { platformBridge } from "../../platform";
import {
  parseAssignments,
  parseRoleEnabled,
  serializeMappings,
  validCandidates,
} from "../shared/roleMapping";
import type { RoleMappingDraft } from "../shared/roleMapping";

const props = defineProps<{
  targetType: "dts" | "blender";
  baseProfile: RoleBindingProfileDetail | null;
  strategyId?: string;
  strategyVersion?: string;
  saveAsCopy?: boolean;
}>();

const emit = defineEmits<{
  close: [];
  saved: [detail: RoleBindingProfileDetail];
}>();

const bridge = platformBridge();
const client = new LocalApiClient(bridge);
const strategies = shallowRef<StrategyLibrarySummary[]>([]);
const catalogs = shallowRef<AssetCatalogSummary[]>([]);
const strategyDetail = shallowRef<StrategyLibraryDetail | null>(null);
const assets = shallowRef<AssetCatalogAssetInfo[]>([]);
const collections = shallowRef<AssetCatalogCollectionInfo[]>([]);
const mappings = shallowRef<RoleMappingDraft>({});
let dependencyEpoch = 0;
onBeforeUnmount(() => { dependencyEpoch++; });
const roleEnabled = ref<Record<string, boolean>>({});
const step = ref(props.baseProfile ? 1 : 0);
const dialogElement = ref<HTMLElement | null>(null);
const parameterValues = ref<Record<string, number>>({});
const parameterStrategyHash = ref("");
const visibleGenerationParameters = computed(() =>
  visibleParameters(
    strategyDetail.value?.generation_parameters ?? [],
    roleEnabled.value,
  ),
);
const displayName = ref(
  props.baseProfile
    ? `${props.baseProfile.summary.display_name}${props.saveAsCopy ? " 副本" : ""}`
    : "",
);
const selectedStrategyHash = ref("");
const loading = ref(true);
const loadingDependency = ref(false);
const saving = ref(false);
const managementAvailable = ref(false);
const errorMessage = ref<string | null>(null);
const initialSignature = ref("");
const previewRoleKey = ref("");
const previewRole = computed(
  () =>
    strategyDetail.value?.roles.find(
      (role) => role.role_key === previewRoleKey.value,
    ) ?? strategyDetail.value?.roles[0],
);
const presentationGroups = computed(() =>
  roleGroups(strategyDetail.value?.roles ?? []),
);
const signature = computed(() =>
  JSON.stringify([
    displayName.value,
    selectedStrategyHash.value,
    mappings.value,
    roleEnabled.value,
    parameterValues.value,
  ]),
);

const strategyOptions = computed(() =>
  props.baseProfile || (props.strategyId && props.strategyVersion)
    ? strategies.value.filter(
        (item) =>
          item.strategy_id ===
            (props.baseProfile?.summary.strategy_id ?? props.strategyId) &&
          item.strategy_version ===
            (props.baseProfile?.summary.resolved_strategy_version ??
              props.baseProfile?.summary.strategy_version ??
              props.strategyVersion),
      )
    : strategies.value,
);
const strategyGroups = computed(() => {
  const groups = new Map<string, StrategyLibrarySummary[]>();
  for (const strategy of strategyOptions.value)
    groups.set(strategy.strategy_id, [
      ...(groups.get(strategy.strategy_id) ?? []),
      strategy,
    ]);
  return [...groups.values()].map((versions) =>
    versions.sort((a, b) =>
      b.strategy_version.localeCompare(a.strategy_version, undefined, {
        numeric: true,
      }),
    ),
  );
});
const mappingReady = computed(
  () =>
    strategyDetail.value !== null &&
    missingRequiredRoles.value.length === 0 &&
    (strategyDetail.value.roles ?? []).some(
      (role) =>
        roleEnabled.value[role.role_key] !== false &&
        validCandidates(mappings.value[role.role_key]),
    ),
);
const selectedStrategy = computed(
  () =>
    strategies.value.find(
      (item) => item.package_hash === selectedStrategyHash.value,
    ) ?? null,
);
const missingRequiredRoles = computed(() =>
  (strategyDetail.value?.roles ?? []).filter(
    (role) =>
      roleEnabled.value[role.role_key] !== false &&
      role.required &&
      !validCandidates(mappings.value[role.role_key]),
  ),
);
const canSave = computed(
  () =>
    managementAvailable.value &&
    displayName.value.trim().length > 0 &&
    selectedStrategy.value !== null &&
    strategyDetail.value !== null &&
    Object.values(serializeMappings(mappings.value, roleEnabled.value)).some(
      (item) => item.enabled !== false,
    ) &&
    missingRequiredRoles.value.length === 0 &&
    parameterValuesValid(
      strategyDetail.value?.generation_parameters ?? [],
      parameterValues.value,
    ) &&
    !loadingDependency.value &&
    !saving.value,
);

onMounted(async () => {
  try {
    const [strategyPage, catalogPage, connection] = await Promise.all([
      client.strategies(),
      client.assetCatalogs(props.targetType),
      bridge.getLocalApiConnection(),
    ]);
    strategies.value = strategyPage.items;
    catalogs.value = catalogPage.items;
    managementAvailable.value =
      connection.ok && connection.value.managementAvailable;
    if (props.baseProfile) {
      selectedStrategyHash.value =
        strategies.value.find(
          (item) =>
            item.strategy_id === props.baseProfile?.summary.strategy_id &&
            item.strategy_version ===
              (props.baseProfile?.summary.resolved_strategy_version ??
                props.baseProfile?.summary.strategy_version),
        )?.package_hash ?? "";
      mappings.value = parseAssignments(props.baseProfile.assignments, props.baseProfile.assets ?? []);
      await loadDependencies(false);
      mappings.value = parseAssignments(
        props.baseProfile.assignments,
        [...(props.baseProfile.assets ?? []), ...assets.value],
      );
      roleEnabled.value = parseRoleEnabled(props.baseProfile.assignments, strategyDetail.value?.roles ?? []);
    } else if (props.strategyId && props.strategyVersion) {
      selectedStrategyHash.value =
        strategies.value.find(
          (item) =>
            item.strategy_id === props.strategyId &&
            item.strategy_version === props.strategyVersion,
        )?.package_hash ?? "";
      await loadDependencies(false);
    }
  } catch (error) {
    errorMessage.value = readableError(error);
  } finally {
    loading.value = false;
    initialSignature.value = signature.value;
    await nextTick();
    dialogElement.value?.focus({ preventScroll: true });
  }
});

const chooseStrategyHash = async (hash: string) => {
  if (selectedStrategyHash.value === hash || saving.value) return;
  if (
    Object.keys(mappings.value).length &&
    !(await confirmAction({
      title: "切换种植策略？",
      message: "当前未保存的植被搭配将被清除。",
      confirmLabel: "切换策略",
      tone: "danger",
    }))
  )
    return;
  selectedStrategyHash.value = hash;
  await loadDependencies(true);
};
const groupVersion = (versions: StrategyLibrarySummary[]) =>
  versions.find((item) => item.package_hash === selectedStrategyHash.value) ??
  versions[0];
const close = async () => {
  if (saving.value) return;
  if (
    signature.value !== initialSignature.value &&
    !(await confirmAction({
      title: "放弃方案修改？",
      message: "未保存的方案配置将被丢弃。",
      confirmLabel: "放弃修改",
      tone: "danger",
    }))
  )
    return;
  emit("close");
};

const loadDependencies = async (clearMappings: boolean) => {
  const epoch = ++dependencyEpoch;
  if (clearMappings) {
    mappings.value = {};
    roleEnabled.value = {};
  }
  strategyDetail.value = null;
  assets.value = [];
  collections.value = [];
  const strategy = selectedStrategy.value;
  if (!strategy) return;
  loadingDependency.value = true;
  errorMessage.value = null;
  try {
    const detail = await client.strategy(strategy.package_hash);
    if (epoch !== dependencyEpoch) return;
    strategyDetail.value = detail;
    if (parameterStrategyHash.value !== selectedStrategyHash.value) {
      parameterValues.value = parameterPresetValues(
        detail?.generation_parameters ?? [],
        props.baseProfile?.strategy_parameters ?? {},
      );
      parameterStrategyHash.value = selectedStrategyHash.value;
    }
    assets.value = props.baseProfile?.assets ?? [];
  } catch (error) {
    if (epoch === dependencyEpoch) errorMessage.value = readableError(error);
  } finally {
    if (epoch === dependencyEpoch) loadingDependency.value = false;
  }
};

const save = async () => {
  const strategy = selectedStrategy.value;
  if (!canSave.value || !strategy) return;
  saving.value = true;
  errorMessage.value = null;
  try {
    const detail = await client.saveRoleBindingProfile({
      profileId: props.saveAsCopy
        ? null
        : (props.baseProfile?.summary.profile_id ?? null),
      sourceProfileId: props.saveAsCopy
        ? props.baseProfile?.summary.profile_id
        : undefined,
      displayName: displayName.value.trim(),
      strategyId: strategy.strategy_id,
      strategyVersion: strategy.strategy_version,
      targetType: props.targetType,
      catalogId: null,
      assignments: serializeMappings(mappings.value, roleEnabled.value),
      strategyParameters: { ...parameterValues.value },
      expectedGeneration: props.saveAsCopy
        ? 0
        : (props.baseProfile?.summary.generation ?? 0),
    });
    emit("saved", detail);
  } catch (error) {
    errorMessage.value = readableError(error);
  } finally {
    saving.value = false;
  }
};

const readableError = (error: unknown) => {
  if (error instanceof LocalApiError) {
    return error.code === "connection_failed"
      ? "无法连接 VerdFlow 本地服务"
      : error.message;
  }
  return error instanceof Error ? error.message : "种植方案保存失败";
};
</script>

<template>
  <div class="studio-modal-backdrop plan-editor-backdrop">
    <section
      ref="dialogElement"
      tabindex="-1"
      class="studio-modal plan-editor-dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="plan-editor-title"
      @keydown.esc.stop="close"
    >
      <header>
        <div>
          <span>种植方案</span>
          <h3 id="plan-editor-title">
            {{
              saveAsCopy
                ? "另存方案副本"
                : baseProfile
                  ? "编辑种植方案"
                  : "新建种植方案"
            }}
          </h3>
        </div>
        <button
          class="icon-button"
          type="button"
          title="关闭"
          :disabled="saving"
          @click="close"
        >
          <X :size="17" />
        </button>
      </header>

      <div v-if="errorMessage" class="inline-alert" role="alert">
        {{ errorMessage }}
      </div>
      <div v-if="loading" class="library-loading">
        <LoaderCircle :size="18" class="spinning" />正在读取依赖
      </div>
      <nav v-if="!loading" class="plan-editor-steps" aria-label="方案配置步骤">
        <button
          v-for="(name, index) in ['选择策略', '植被搭配', '种植效果']"
          :key="name"
          type="button"
          :class="{ active: step === index }"
          :aria-current="step === index ? 'step' : undefined"
          :disabled="
            saving ||
            loadingDependency ||
            (index > 0 && !strategyDetail) ||
            (index > 1 && !mappingReady)
          "
          @click="step = index"
        >
          <span
            ><Check v-if="step > index" :size="13" /><template v-else>{{
              index + 1
            }}</template></span
          >{{ name }}
        </button>
      </nav>
      <div
        v-if="!loading"
        class="plan-editor-body"
        :class="{ 'is-mapping': step === 1 }"
      >
        <section v-if="step === 0" class="plan-editor-strategies">
          <aside class="plan-strategy-library">
            <header>
              <h4>空间构图</h4>
              <span>{{ strategyGroups.length }} 种策略</span>
            </header>
            <div
              class="plan-editor-strategy-grid"
              role="radiogroup"
              aria-label="种植策略"
            >
              <article
                v-for="versions in strategyGroups"
                :key="versions[0].strategy_id"
                class="plan-strategy-choice"
                :class="{
                  selected: versions.some(
                    (item) => item.package_hash === selectedStrategyHash,
                  ),
                }"
              >
                <button
                  type="button"
                  role="radio"
                  :aria-checked="
                    versions.some(
                      (item) => item.package_hash === selectedStrategyHash,
                    )
                  "
                  :disabled="saving || loadingDependency"
                  @click="
                    chooseStrategyHash(groupVersion(versions).package_hash)
                  "
                >
                  <span class="plan-strategy-symbol"
                    ><Sprout :size="23"
                  /></span>
                  <span class="plan-strategy-choice-copy"
                    ><strong>{{ groupVersion(versions).display_name }}</strong
                    ><small
                      >v{{ groupVersion(versions).strategy_version }} ·
                      {{ versions.length }} 个版本</small
                    ></span
                  >
                  <CircleCheck
                    v-if="
                      versions.some(
                        (item) => item.package_hash === selectedStrategyHash,
                      )
                    "
                    :size="19"
                  />
                  <span
                    v-else
                    class="plan-strategy-radio"
                    aria-hidden="true"
                  ></span>
                </button>
              </article>
            </div>
          </aside>
          <section
            v-if="strategyDetail && selectedStrategy"
            class="plan-strategy-preview"
          >
            <header class="plan-strategy-preview-heading">
              <div>
                <span>当前策略</span>
                <h4>{{ selectedStrategy.display_name }}</h4>
              </div>
              <label
                ><span>策略版本</span
                ><select
                  :value="selectedStrategyHash"
                  :disabled="saving || loadingDependency"
                  @change="
                    chooseStrategyHash(
                      ($event.target as HTMLSelectElement).value,
                    )
                  "
                >
                  <option
                    v-for="version in strategyGroups.find(
                      (versions) =>
                        versions[0].strategy_id ===
                        selectedStrategy?.strategy_id,
                    ) ?? []"
                    :key="version.package_hash"
                    :value="version.package_hash"
                  >
                    v{{ version.strategy_version
                    }}{{ version.executable ? "" : " · 未信任" }}
                  </option>
                </select></label
              >
            </header>
            <p class="plan-editor-strategy-description">
              {{ strategyDetail.description }}
            </p>
            <dl class="plan-strategy-facts">
              <div>
                <dt>植被用途</dt>
                <dd>{{ strategyDetail.roles.length }}</dd>
              </div>
              <div>
                <dt>用途分组</dt>
                <dd>{{ presentationGroups.length }}</dd>
              </div>
              <div>
                <dt>效果参数</dt>
                <dd>{{ strategyDetail.generation_parameters.length }}</dd>
              </div>
            </dl>
            <nav class="plan-strategy-role-tabs" aria-label="策略植被用途">
              <button
                v-for="role in strategyDetail.roles"
                :key="role.role_key"
                type="button"
                :class="{ active: previewRole?.role_key === role.role_key }"
                :aria-pressed="previewRole?.role_key === role.role_key"
                @click="previewRoleKey = role.role_key"
              >
                <i
                  :style="{
                    background: roleColor(strategyDetail.roles, role.role_key),
                  }"
                />{{ role.display_name }}
              </button>
            </nav>
            <div v-if="previewRole" class="plan-strategy-purpose">
              <strong>{{ previewRole.display_name }}</strong>
              <p>{{ previewRole.description }}</p>
              <RolePurposePanel
                :role="previewRole"
                :roles="strategyDetail.roles"
              />
            </div>
          </section>
          <div v-else class="plan-strategy-placeholder">
            <LoaderCircle
              v-if="loadingDependency"
              :size="28"
              class="spinning"
            /><Sprout v-else :size="36" /><strong>{{
              loadingDependency ? "正在读取策略" : "选择种植策略"
            }}</strong>
          </div>
          <p v-if="!strategyGroups.length" class="planting-empty">
            对应策略尚未安装，请先导入策略包。
          </p>
        </section>
        <template v-else-if="step === 1">
          <div v-if="loadingDependency" class="library-loading">
            <LoaderCircle :size="18" class="spinning" />正在读取植被
          </div>
          <PlanMappingWorkspace
            v-else
            v-model:enabled="roleEnabled"
            v-model:mappings="mappings"
            :roles="strategyDetail?.roles ?? []"
            :assets="assets"
            :catalogs="catalogs"
            :collections="collections"
            :disabled="saving"
          />
        </template>
        <section v-else class="plan-editor-parameters">
          <label class="plan-editor-name"
            ><span>方案名称</span
            ><input
              v-model="displayName"
              maxlength="80"
              placeholder="例如：草坪铺设"
              :disabled="saving"
          /></label>
          <StrategyParameterEditor
            simplified
            :parameters="visibleGenerationParameters"
            :values="parameterValues"
            :show-seed="false"
            :disabled="saving"
            @update-parameter="
              (key, value) =>
                (parameterValues = { ...parameterValues, [key]: value })
            "
            @reset="
              parameterValues = parameterPresetValues(
                strategyDetail?.generation_parameters ?? [],
              )
            "
          />
        </section>
      </div>

      <footer>
        <span v-if="!managementAvailable">当前桌面连接没有资源库管理权限</span>
        <span v-else-if="missingRequiredRoles.length"
          >待配置：{{
            missingRequiredRoles.map((role) => role.display_name).join("、")
          }}</span
        >
        <span v-else
          >{{ selectedStrategy?.display_name
          }}{{
            selectedStrategy ? ` · v${selectedStrategy.strategy_version}` : ""
          }}</span
        >
        <button
          class="secondary-button"
          type="button"
          :disabled="saving"
          @click="step > 0 ? step-- : close()"
        >
          <ArrowLeft v-if="step > 0" :size="15" />{{
            step > 0 ? "上一步" : "取消"
          }}
        </button>
        <button
          v-if="step < 2"
          class="primary-button"
          type="button"
          :disabled="
            saving ||
            loadingDependency ||
            !strategyDetail ||
            (step === 1 && !mappingReady)
          "
          @click="step++"
        >
          {{ step === 0 ? "选择植被" : "调整种植效果"
          }}<ArrowRight :size="15" />
        </button>
        <button
          v-else
          class="primary-button"
          type="button"
          :disabled="!canSave"
          @click="save"
        >
          <LoaderCircle v-if="saving" :size="16" class="spinning" />
          <Save v-else :size="16" />
          保存种植方案
        </button>
      </footer>
    </section>
  </div>
</template>
