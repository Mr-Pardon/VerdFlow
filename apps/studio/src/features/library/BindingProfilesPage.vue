<script setup lang="ts">
import {
  ArrowLeft,
  CopyPlus,
  Download,
  GitMerge,
  LoaderCircle,
  Pencil,
  RefreshCw,
  Trash2,
  Trees,
} from "@lucide/vue";
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import BindingProfileEditor from "./BindingProfileEditor.vue";
import { confirmAction } from "../../components/confirmDialog";
import LibraryThumbnail from "./LibraryThumbnail.vue";
import { weightPercentages } from "../shared/roleMapping";
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
    }));
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
        displayName: asset?.display_name ?? snapshotName,
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
    return `${scalePercent(scale.min)}%–${scalePercent(scale.max)}%`;
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
        <p>
          {{ library.strategyDisplayName.value }}
          · v{{ library.selectedProfile.value?.strategy_version }}
        </p>
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
          <header>
            <div>
              <span>方案配置</span>
              <h2>{{ library.profileDetail.value.summary.display_name }}</h2>
              <p>{{ library.profileDetail.value.summary.profile_id }}</p>
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

          <section class="binding-dependencies" aria-label="映射依赖">
            <div>
              <small>当前策略版本</small
              ><strong :title="library.strategyDisplayName.value">{{
                library.strategyDisplayName.value
              }}</strong
              ><span>{{
                library.profileDetail.value.summary.resolved_strategy_version ??
                library.profileDetail.value.summary.strategy_version
              }}</span>
              <small v-if="library.profileDetail.value.summary.resolved_strategy_version &&
                library.profileDetail.value.summary.resolved_strategy_version !== library.profileDetail.value.summary.strategy_version">
                保存于 v{{ library.profileDetail.value.summary.strategy_version }} · 已适配兼容新版
              </small>
              <small v-if="library.profileDetail.value.summary.strategy_resolution_issue">
                {{ library.profileDetail.value.summary.strategy_resolution_issue }}
              </small>
            </div>
            <GitMerge :size="18" />
            <div>
              <small>输出 Provider</small
              ><strong>{{
                library.profileDetail.value.summary.target_type.toUpperCase()
              }}</strong
              ><span>{{
                library.profileDetail.value.summary.provider_id
              }}</span>
            </div>
            <GitMerge :size="18" />
            <div>
              <small>来源资产库</small
              ><strong :title="library.catalogDisplayName.value">{{
                library.catalogDisplayName.value
              }}</strong
              ><span>方案可独立使用</span>
            </div>
          </section>

          <section class="binding-assignment-section">
            <details open class="profile-parameter-summary">
              <summary>
                种植效果 · {{ parameterRows.length }} 项{{
                  library.profileDetail.value.strategy_parameters == null
                    ? " · 策略默认值"
                    : ""
                }}
              </summary>
              <dl>
                <div v-for="parameter in parameterRows" :key="parameter.key">
                  <dt>{{ parameter.name }}</dt>
                  <dd>{{ parameter.value }}</dd>
                </div>
              </dl>
            </details>
            <header>
              <div>
                <span>Role 映射</span>
                <h3>植被分配</h3>
              </div>
              <strong>{{ library.profileDetail.value.role_count }}</strong>
            </header>
            <div class="profile-assignment-list">
              <section v-for="row in assignmentRows" :key="row.roleKey">
                <header>
                  <strong>{{ row.roleName }}</strong
                  ><small>{{
                    row.enabled ? `${row.candidates.length} 个候选` : "已关闭"
                  }}</small>
                </header>
                <div
                  v-for="candidate in row.candidates"
                  :key="candidate.assetId"
                  class="profile-assignment-asset"
                >
                  <LibraryThumbnail
                    :resource-id="candidate.thumbnailResourceId"
                    :alt="candidate.displayName"
                  >
                    <span class="profile-assignment-thumbnail"
                      ><Trees :size="14"
                    /></span>
                  </LibraryThumbnail>
                  <span>
                    <strong>{{ candidate.displayName }}</strong>
                    <small
                      >权重 {{ candidate.weightPercent }}% · 缩放
                      {{ candidate.scale }}</small
                    >
                    <em v-if="candidate.drift !== 'current'">{{
                      driftLabel(candidate.drift)
                    }}</em>
                  </span>
                </div>
              </section>
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
