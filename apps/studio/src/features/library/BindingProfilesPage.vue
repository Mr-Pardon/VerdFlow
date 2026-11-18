<script setup lang="ts">
import {
  ArrowLeft,
  CopyPlus,
  ChevronDown,
  Download,
  GitMerge,
  LoaderCircle,
  Pencil,
  RefreshCw,
  SlidersHorizontal,
  Trash2,
  Trees,
} from "@lucide/vue";
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { roleColor } from "@verdflow/role-guide-ui";

import BindingProfileEditor from "./BindingProfileEditor.vue";
import { confirmAction } from "../../components/confirmDialog";
import StrategyExamplePreview from "../../components/StrategyExamplePreview.vue";
import LibraryThumbnail from "./LibraryThumbnail.vue";
import { parseAssignments, parseRoleEnabled, weightPercentages } from "../shared/roleMapping";
import { strategyExampleAppearance } from "../shared/strategyExampleAppearance";
import { useBindingProfileLibrary } from "./useBindingProfileLibrary";

const route = useRoute();
const router = useRouter();
const targetType = route.params.target === "blender" ? "blender" : "dts";
const library = useBindingProfileLibrary({
  targetType,
  profileId: () => String(route.params.profileId ?? ""),
});
const editorProfile = ref<
  import("../../api").RoleBindingProfileDetail | null | undefined
>();
const copyMode = ref(false);

const assignmentRows = computed(() =>
  Object.entries(library.profileDetail.value?.assignments ?? {}).map(
    ([roleKey, assignment]) => ({
      roleKey,
      enabled: assignment.enabled !== false,
      roleName: library.roleNames.value[roleKey] ?? roleKey,
      candidates: candidatesFrom(roleKey, assignment),
    }),
  ),
);
const parameterRows = computed(() => {
  const preset = library.profileDetail.value?.strategy_parameters ?? {};
  const definitions = library.generationParameters.value;
  if (!definitions.length)
    return Object.entries(preset).map(([key, value]) => ({
      key,
      name: key,
      value,
      saved: true,
    }));
  return definitions
    .filter(
      (parameter) =>
        !parameter.visible_for_roles?.length ||
        parameter.visible_for_roles.some(
          (key) =>
            library.profileDetail.value?.assignments[key]?.enabled !== false,
        ),
    )
    .map((parameter) => ({
      key: parameter.key,
      name: parameter.display_name,
      value: preset[parameter.key] ?? parameter.default,
      saved: Object.hasOwn(preset, parameter.key),
    }));
});
const previewStrategy = library.strategyDetail;
const previewValues = computed(() => library.profileDetail.value?.strategy_parameters ?? {});
const previewEnabled = computed(() => parseRoleEnabled(library.profileDetail.value?.assignments ?? {}, previewStrategy.value?.roles));
const previewUnavailable = computed(() => {
  const strategy = previewStrategy.value;
  if (!strategy) return "尚未安装此方案可用的策略。";
  if (library.profileDetail.value?.summary.strategy_resolution_issue) return library.profileDetail.value.summary.strategy_resolution_issue;
  if (!strategy.summary.executable) return "策略可执行后才能生成动态示意。";
  if (!strategy.parameter_preview) return "当前策略没有提供示例。";
  if (!library.managementAvailable.value) return "当前运行方式不支持动态示意。";
  if (!strategy.roles.some(role => previewEnabled.value[role.role_key] !== false)) return "没有启用的植被用途。";
  if (strategy.generation_parameters.some(field => {
    const value = previewValues.value[field.key] ?? field.default;
    return !Number.isFinite(value) || value < field.minimum || value > field.maximum;
  })) return "方案参数超出当前策略的示例范围。";
  return "";
});
const previewAppearance = computed(() => {
  if (previewUnavailable.value) return [];
  const assignments = library.profileDetail.value?.assignments ?? {};
  const assets = Object.values(library.assetDetails.value);
  return strategyExampleAppearance(parseAssignments(assignments, assets), assignments, assets);
});

onMounted(() => library.initialize());
watch(
  () => route.params.profileId,
  () => {
    editorProfile.value = undefined;
    void library.refreshTarget();
  },
);

const backToStrategies = () => router.push({ name: "planting-plans" });

const openEdit = () => {
  copyMode.value = false;
  if (library.profileDetail.value)
    editorProfile.value = library.profileDetail.value;
};
const closeEditor = () => {
  editorProfile.value = undefined;
};
const profileSaved = async (
  detail: import("../../api").RoleBindingProfileDetail,
) => {
  closeEditor();
  if (detail.summary.profile_id !== route.params.profileId) {
    await router.push({
      name: "planting-plan",
      params: { profileId: detail.summary.profile_id },
    });
    return;
  }
  await library.profileSaved(detail);
};
const deleteProfile = async () => {
  const profile = library.selectedProfile.value;
  if (!profile) return;
  const confirmed = await confirmAction({
    title: "删除种植方案？",
    message: `将删除“${profile.display_name}”。历史运行不会受影响。`,
    confirmLabel: "删除方案",
    tone: "danger",
  });
  if (confirmed) {
    await library.deleteSelectedProfile();
    if (!library.errorMessage.value) await backToStrategies();
  }
};

const candidatesFrom = (
  roleKey: string,
  assignment: Record<string, unknown>,
) => {
  if (!Array.isArray(assignment.candidates)) return [];
  const candidates = assignment.candidates.flatMap((candidate) => {
    if (!candidate || typeof candidate !== "object") return [];
    const item = candidate as Record<string, unknown>;
    if (typeof item.asset_id !== "string") return [];
    const asset = library.assetDetails.value[item.asset_id];
    const snapshotName =
      typeof item.display_name_snapshot === "string"
        ? item.display_name_snapshot
        : item.asset_id;
    return [
      {
        assetId: item.asset_id,
        displayName: typeof item.display_name_snapshot === "string" ? snapshotName : asset?.display_name ?? snapshotName,
        thumbnailResourceId: asset?.thumbnail_resource_id ?? null,
        weight: typeof item.weight === "number" ? item.weight : 1,
        scale: scaleLabel(item.scale),
        drift:
          library.profileDetail.value?.catalog_drift[
            `${roleKey}/${item.asset_id}`
          ] ?? "unlinked",
      },
    ];
  });
  const percentages = weightPercentages(
    candidates.map((candidate) => candidate.weight),
  );
  return candidates.map((candidate, index) => ({
    ...candidate,
    weightPercent: percentages[index],
  }));
};

const scaleLabel = (value: unknown) => {
  if (!value || typeof value !== "object" || Array.isArray(value))
    return "固定 100%";
  const scale = value as Record<string, unknown>;
  if (scale.mode === "fixed") return `固定 ${scalePercent(scale.value)}%`;
  if (scale.mode === "uniform_random") {
    return `随机 ${scalePercent(scale.min)}%–${scalePercent(scale.max)}%`;
  }
  return "固定 100%";
};

const scalePercent = (value: unknown) =>
  typeof value === "number" && Number.isFinite(value)
    ? Math.round(value * 100)
    : "-";

const driftLabel = (value: string) =>
  ({
    metadata_changed: "资产信息有更新",
    target_changed: "目标引用有变化",
    missing: "未在当前资产库找到",
    unlinked: "未关联资产库",
  })[value] ?? "";
</script>

<template>
  <section class="library-page binding-profiles-page">
    <header class="page-header library-page__header">
      <div>
        <span class="page-header__eyebrow">种植方案</span>
        <h1>{{ library.selectedProfile.value?.display_name || "方案详情" }}</h1>
        <p v-if="library.strategySummary.value">{{ library.strategyDisplayName.value }} · v{{ library.strategySummary.value.strategy_version }}</p>
      </div>
      <div class="page-actions">
        <button
          class="secondary-button"
          type="button"
          @click="backToStrategies"
        >
          <ArrowLeft :size="16" />返回方案库
        </button>
        <button
          class="icon-button"
          type="button"
          title="刷新"
          @click="() => library.refreshTarget()"
        >
          <RefreshCw :size="17" :class="{ spinning: library.loading.value }" />
        </button>
        <button
          class="secondary-button"
          type="button"
          :disabled="
            !library.managementAvailable.value ||
            !library.strategySummary.value ||
            !library.profileDetail.value
          "
          @click="
            copyMode = true;
            editorProfile = library.profileDetail.value;
          "
        >
          <CopyPlus :size="16" />另存副本
        </button>
      </div>
    </header>

    <div
      v-if="library.errorMessage.value"
      class="inline-alert library-alert"
      role="alert"
    >
      <div>
        <strong>种植方案暂时不可用</strong>
        <p>{{ library.errorMessage.value }}</p>
      </div>
      <button type="button" @click="() => library.refreshTarget()">
        重新读取
      </button>
    </div>
    <div
      v-else-if="library.statusMessage.value"
      class="inline-alert inline-alert--success library-alert"
      role="status"
    >
      {{ library.statusMessage.value }}
    </div>

    <div
      class="library-workspace binding-profile-workspace plan-detail-workspace"
    >
      <main class="binding-profile-detail">
        <div
          v-if="library.loadingDetail.value"
          class="library-loading library-loading--fill"
        >
          <LoaderCircle :size="20" class="spinning" />正在读取方案
        </div>
        <template v-else-if="library.profileDetail.value">
          <header class="plan-detail-toolbar">
            <div class="plan-detail-facts">
              <div><small>输出平台</small><strong :title="library.profileDetail.value.summary.provider_id">{{ library.profileDetail.value.summary.target_type.toUpperCase() }}</strong></div>
              <div><small>来源资产库</small><strong :title="library.catalogDisplayName.value">{{ library.catalogDisplayName.value }}</strong></div>
            </div>
            <div class="binding-profile-detail-actions">
              <span
                v-if="library.profileDetail.value.drift_count"
                class="binding-profile-active"
                >{{ library.profileDetail.value.drift_count }} 项提示</span
              >
              <button
                v-if="library.syncableMetadataCount.value"
                class="secondary-button"
                type="button"
                title="将资产库中的最新名称与参考半径同步到当前植被搭配，不改变目标引用"
                :disabled="
                  !library.managementAvailable.value || library.mutating.value
                "
                @click="library.syncSelectedAssetMetadata"
              >
                <RefreshCw
                  :size="15"
                  :class="{ spinning: library.mutating.value }"
                />同步资产信息
              </button>
              <button
                class="secondary-button"
                type="button"
                :disabled="
                  !library.managementAvailable.value ||
                  !library.strategySummary.value ||
                  library.mutating.value
                "
                @click="openEdit"
              >
                <Pencil :size="15" />编辑
              </button>
              <button
                class="secondary-button"
                type="button"
                :disabled="
                  !library.managementAvailable.value || library.mutating.value
                "
                @click="library.exportSelected"
              >
                <Download :size="15" />导出 .vfp
              </button>
              <button
                class="icon-button icon-button--danger"
                type="button"
                title="删除种植方案"
                :disabled="
                  !library.managementAvailable.value || library.mutating.value
                "
                @click="deleteProfile"
              >
                <Trash2 :size="15" />
              </button>
            </div>
          </header>

          <section class="plan-detail-section">
            <header><Trees :size="19" /><h3>植被搭配</h3><small>{{ assignmentRows.filter(row => row.enabled).length }} 项用途启用</small></header>
            <div class="plan-detail-roles">
              <details v-for="row in assignmentRows" :key="row.roleKey" :open="row.enabled">
                <summary>
                  <i :style="{ backgroundColor: roleColor(previewStrategy?.roles ?? [], row.roleKey) }" />
                  <strong>{{ row.roleName }}</strong><small>{{ row.enabled ? `${row.candidates.length} 种` : '已关闭' }}</small><ChevronDown :size="15" />
                </summary>
                <div v-if="row.candidates.length" class="plan-detail-candidates">
                  <div v-for="candidate in row.candidates" :key="candidate.assetId" class="plan-detail-asset">
                    <div class="plan-detail-thumb"><LibraryThumbnail :resource-id="candidate.thumbnailResourceId" alt=""><Trees :size="18" /></LibraryThumbnail></div>
                    <span>
                      <strong>{{ candidate.displayName }}</strong>
                      <small>权重 {{ candidate.weightPercent }}% · {{ candidate.scale }}</small>
                      <em v-if="driftLabel(candidate.drift)">{{ driftLabel(candidate.drift) }}</em>
                    </span>
                  </div>
                </div>
                <p v-else class="plan-detail-empty">没有已保存的候选资产。</p>
              </details>
            </div>
          </section>
          <section class="plan-detail-section plan-detail-effect">
            <header><SlidersHorizontal :size="19" /><h3>种植效果</h3></header>
            <div class="plan-detail-effect-grid">
              <div>
                <dl v-if="parameterRows.length" class="plan-detail-parameters" aria-label="方案参数">
                  <div v-for="parameter in parameterRows" :key="parameter.key">
                    <dt>{{ parameter.name }}</dt><dd><strong>{{ parameter.value }}</strong><small>{{ parameter.saved ? '方案保存' : '策略默认' }}</small></dd>
                  </div>
                </dl>
                <p v-else class="plan-detail-empty">没有可显示的参数。</p>
              </div>
              <StrategyExamplePreview v-if="previewStrategy && !previewUnavailable" :key="previewStrategy.summary.package_hash"
                :strategy="previewStrategy" :values="previewValues" :enabled="previewEnabled" :appearance="previewAppearance" />
              <div v-else class="plan-detail-preview-empty"><strong>效果示意不可用</strong><p>{{ previewUnavailable }}</p></div>
            </div>
          </section>
        </template>
        <div v-else class="library-empty library-empty--detail">
          <GitMerge :size="30" /><strong>选择一个植被搭配</strong>
        </div>
      </main>
    </div>

    <BindingProfileEditor
      v-if="editorProfile !== undefined"
      :target-type="library.targetType.value"
      :base-profile="editorProfile"
      :save-as-copy="copyMode"
      @close="closeEditor"
      @saved="profileSaved"
    />
  </section>
</template>

<style scoped>
.plan-detail-section { padding: 18px 24px 22px; border-bottom: 1px solid var(--color-border); }
.binding-profile-detail > .plan-detail-toolbar { padding: 14px 24px; align-items: center; flex-wrap: wrap; gap: 12px; background: var(--color-surface-soft); }
.plan-detail-facts { display: flex; flex-wrap: wrap; gap: 8px 18px; min-width: 0; }
.plan-detail-facts > div { display: flex; flex-wrap: wrap; gap: 3px 7px; align-items: baseline; min-width: 0; }
.plan-detail-facts small { color: var(--color-text-muted); font-size: 12px; }
.plan-detail-facts strong { font-size: 13px; overflow-wrap: anywhere; }
.plan-detail-toolbar .binding-profile-detail-actions { flex: 0 1 auto; flex-wrap: wrap; }
.plan-detail-section > header { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; color: var(--color-accent); }
.plan-detail-section > header h3 { margin: 0; font-size: 16px; color: var(--color-text); }
.plan-detail-section > header small { margin-left: auto; color: var(--color-text-muted); }
.plan-detail-roles { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(310px, 100%), 1fr)); align-items: start; gap: 8px; }
.plan-detail-roles > details { min-width: 0; border: 1px solid var(--color-border); border-radius: 6px; }
.plan-detail-roles summary { display: flex; align-items: center; gap: 8px; padding: 9px 12px; font-size: 13px; cursor: pointer; list-style: none; }
.plan-detail-roles summary::-webkit-details-marker { display: none; }
.plan-detail-roles summary i { width: 8px; height: 8px; border-radius: 50%; flex: 0 0 8px; }
.plan-detail-roles summary small { margin-left: auto; color: var(--color-text-muted); }
.plan-detail-roles details[open] summary svg { transform: rotate(180deg); }
.plan-detail-candidates { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(205px, 100%), 1fr)); gap: 6px 18px; padding: 0 12px 10px; }
.plan-detail-asset { min-width: 0; display: flex; align-items: center; gap: 9px; padding: 7px 0; border-top: 1px solid var(--color-border); }
.plan-detail-asset span { min-width: 0; overflow-wrap: anywhere; }
.plan-detail-asset strong, .plan-detail-asset small, .plan-detail-asset em { display: block; }
.plan-detail-asset strong { font-size: 13px; }
.plan-detail-asset small, .plan-detail-asset em { margin-top: 3px; font-size: 11px; color: var(--color-text-muted); font-style: normal; }
.plan-detail-asset em { color: #946330; }
.plan-detail-thumb { width: 38px; height: 38px; flex: 0 0 38px; display: grid; place-items: center; overflow: hidden; border-radius: 4px; color: var(--color-accent); background: var(--color-accent-soft); }
.plan-detail-thumb :deep(img) { display: block; width: 100%; height: 100%; object-fit: cover; }
.plan-detail-effect-grid { display: grid; grid-template-columns: minmax(0, 0.8fr) minmax(0, 1.2fr); gap: 18px; align-items: start; }
.plan-detail-parameters { display: grid; gap: 7px; margin: 0; }
.plan-detail-parameters > div { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 10px; align-items: center; padding: 10px 12px; border: 1px solid var(--color-border); border-radius: 5px; background: var(--color-surface-soft); }
.plan-detail-parameters dt { font-size: 13px; overflow-wrap: anywhere; }
.plan-detail-parameters dd { margin: 0; display: grid; gap: 3px; text-align: right; font-variant-numeric: tabular-nums; }
.plan-detail-parameters dd small, .plan-detail-empty, .plan-detail-preview-empty p { font-size: 12px; color: var(--color-text-muted); }
.plan-detail-empty { margin: 8px 12px 10px; }
.plan-detail-preview-empty { padding: 13px; border: 1px solid var(--color-border); border-radius: 6px; }
.plan-detail-preview-empty p { margin: 6px 0 0; }
@media (max-width: 850px) { .plan-detail-effect-grid { grid-template-columns: minmax(0, 1fr); } }
</style>
