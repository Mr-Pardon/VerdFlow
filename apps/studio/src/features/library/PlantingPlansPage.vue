<script setup lang="ts">
import {
  FileInput,
  LoaderCircle,
  Plus,
  RefreshCw,
  Search,
  Sprout,
} from "@lucide/vue";
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { LocalApiClient } from "../../api";
import { platformBridge } from "../../platform";
import BindingProfileEditor from "./BindingProfileEditor.vue";
import PlantingPlanCard from "./PlantingPlanCard.vue";
import { usePlantingPlans } from "./usePlantingPlans";
import { usePackageDropTarget } from "./usePackageDropTarget";
import { useGuidePage } from "../onboarding/context";
import {
  importPackagesSequentially,
  packageImportFailureMessage,
  unsupportedPackageFailures,
} from "./packageBatchImport";

const router = useRouter();
const library = usePlantingPlans();
const bridge = platformBridge();
const client = new LocalApiClient(bridge);
const creating = ref(false);
const importing = ref(false);
const managementAvailable = ref(false);
const notice = ref("");
useGuidePage("plans", () => ({
  ready: library.plans.value.length > 0,
  status: `当前有 ${library.plans.value.length} 个种植方案`,
  loading: library.loading.value, busy: importing.value || creating.value, error: library.error.value,
  actions: [{ label: "导入 .vfp 方案", disabled: !managementAvailable.value || !bridge.capabilities.selectSourceFiles, run: () => importPlans() }],
}));
onMounted(async () => {
  const connection = await bridge.getLocalApiConnection();
  managementAvailable.value =
    connection.ok && connection.value.managementAvailable;
  await library.refresh();
});
const importPaths = async (
  paths: readonly string[],
  rejected: readonly string[] = [],
  pathFailures: readonly string[] = [],
) => {
  if (importing.value || !managementAvailable.value) return;
  importing.value = true;
  notice.value = "";
  library.error.value = "";
  try {
    const result = await importPackagesSequentially(
      paths,
      (path) => client.importRoleBindingProfilePackage(path),
      (error) => (error instanceof Error ? error.message : String(error)),
    );
    const failures = [
      ...unsupportedPackageFailures(rejected, "vfp"),
      ...result.failures,
      ...pathFailures.map((message, index) => ({
        ordinal: index + 1,
        sourceName: "拖入文件",
        message,
      })),
    ];
    await library.refresh();
    notice.value = result.imported.length
      ? `已导入 ${result.imported.length} 个种植方案`
      : "";
    if (failures.length)
      library.error.value = packageImportFailureMessage(
        "种植方案",
        failures,
        result.imported.length,
      );
  } catch (error) {
    library.error.value =
      error instanceof Error ? error.message : String(error);
  } finally {
    importing.value = false;
  }
};
const importPlans = async () => {
  const selected = await bridge.selectSourceFiles(["vfp"]);
  if (selected.ok) await importPaths(selected.value);
  else if (selected.reason !== "cancelled")
    library.error.value = selected.message;
};
const drop = usePackageDropTarget({
  extension: "vfp",
  disabled: () => importing.value || !managementAvailable.value,
  onDropPaths: ({ paths, rejectedNames, pathFailures }) =>
    importPaths(paths, rejectedNames, pathFailures),
});
</script>

<template>
  <section
    class="library-page plan-library-page package-drop-target"
    @dragenter="drop.onDragEnter"
    @dragover="drop.onDragOver"
    @dragleave="drop.onDragLeave"
    @drop="drop.onDrop"
  >
    <div
      v-if="drop.active.value"
      class="package-drop-overlay"
      aria-hidden="true"
    >
      <FileInput :size="26" /><strong>释放以导入种植方案</strong
      ><span>支持一个或多个 .vfp 文件</span>
    </div>
    <header class="page-header library-page__header">
      <div>
        <span class="page-header__eyebrow">快捷种植</span>
        <h1>种植方案</h1>
      </div>
      <div class="page-actions">
        <button
          class="icon-button"
          type="button"
          title="刷新方案"
          :disabled="library.loading.value"
          @click="library.refresh"
        >
          <RefreshCw :size="17" :class="{ spinning: library.loading.value }" />
        </button>
        <button
          class="secondary-button"
          type="button"
          :disabled="
            !managementAvailable ||
            importing ||
            !bridge.capabilities.selectSourceFiles
          "
          @click="importPlans"
          data-guide-anchor="plans"
        >
          <FileInput :size="17" />{{ importing ? "正在导入" : "导入方案" }}
        </button>
        <button
          class="primary-button"
          type="button"
          :disabled="!managementAvailable"
          @click="creating = true"
        >
          <Plus :size="17" />新建种植方案
        </button>
      </div>
    </header>
    <div class="plan-library-controls">
      <label class="search-control"
        ><Search :size="17" /><input
          v-model="library.query.value"
          type="search"
          placeholder="搜索方案、策略或植被"
          aria-label="搜索种植方案"
      /></label>
      <select v-model="library.strategyFilter.value" aria-label="筛选策略">
        <option value="">全部策略</option>
        <option
          v-for="[id, name] in library.strategyNames.value"
          :key="id"
          :value="id"
        >
          {{ name }}
        </option>
      </select>
      <span>{{ library.filtered.value.length }} 个方案</span>
    </div>
    <p v-if="library.error.value" class="inline-alert" role="alert">
      {{ library.error.value }}
    </p>
    <p v-if="notice" class="inline-alert inline-alert--success" role="status">
      {{ notice }}
    </p>
    <div
      class="plan-library-results"
      role="region"
      aria-label="种植方案列表"
      tabindex="0"
    >
      <div
        v-if="library.loading.value && !library.plans.value.length"
        class="library-loading"
      >
        <LoaderCircle :size="20" class="spinning" />正在读取种植方案
      </div>
      <div v-else-if="library.filtered.value.length" class="plan-card-grid">
        <PlantingPlanCard
          v-for="plan in library.filtered.value"
          :key="plan.profile_id"
          :plan="plan"
          :strategy-name="library.strategyFor(plan)?.display_name"
          :unavailable="
            plan.strategy_resolution_issue || (!library.strategyFor(plan)
              ? '对应策略尚未安装'
              : !library.strategyFor(plan)?.executable
                ? '使用前需信任策略'
                : undefined)
          "
          @choose="
            router.push({
              name: 'planting-plan',
              params: { profileId: plan.profile_id },
            })
          "
        />
      </div>
      <div v-else class="plan-library-empty">
        <Sprout :size="34" />
        <h2>
          {{ library.plans.value.length ? "没有匹配的方案" : "暂无种植方案" }}
        </h2>
        <button
          v-if="!library.plans.value.length"
          class="primary-button"
          type="button"
          :disabled="!managementAvailable"
          @click="creating = true"
        >
          <Plus :size="16" />新建种植方案
        </button>
      </div>
    </div>
    <BindingProfileEditor
      v-if="creating"
      target-type="dts"
      :base-profile="null"
      @close="creating = false"
      @saved="
        (detail) => {
          creating = false;
          router.push({
            name: 'planting-plan',
            params: { profileId: detail.summary.profile_id },
          });
        }
      "
    />
  </section>
</template>
