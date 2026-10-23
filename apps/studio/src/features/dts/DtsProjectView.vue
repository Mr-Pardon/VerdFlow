<script setup lang="ts">
import {
  ArrowLeft,
  Check,
  CircleAlert,
  Clock3,
  FileSearch,
  Layers3,
  MapPinned,
  RefreshCw,
  Search,
  Sprout,
  Trees,
  Upload,
} from "@lucide/vue";
import { computed, ref, watch } from "vue";
import { onBeforeRouteLeave, useRoute, useRouter } from "vue-router";

import type {
  MaterialClassification,
  MaterialFactItem,
  PlantPreviewInputInfo,
  PlantRunExecutionInfo,
  ProjectTaskInfo,
} from "../../api";
import { LocalApiClient } from "../../api";
import { confirmAction } from "../../components/confirmDialog";
import { platformBridge } from "../../platform";
import PlantingStage from "./PlantingStage.vue";
import PlantingTaskStatus from "./PlantingTaskStatus.vue";
import PointReviewStage from "./PointReviewStage.vue";
import RegionStage from "./RegionStage.vue";
import CompositionOutputStage from "./CompositionOutputStage.vue";
import type { PreviewBatchSelection } from "./compositionOutput";
import { useOutputAssetSelection } from "./useOutputAssetSelection";
import { useDtsWorkspace } from "./useDtsWorkspace";

const route = useRoute();
const router = useRouter();
const projectId = computed(() => String(route.params.projectId || ""));
const workspace = useDtsWorkspace(projectId);
const searchQuery = ref("");
type WorkspaceStage =
  | "model"
  | "green"
  | "regions"
  | "planting"
  | "review"
  | "output";
const activeStage = ref<WorkspaceStage>("model");
const selectedRegionIds = ref<string[]>([]);
const plantPreview = ref<PlantRunExecutionInfo | null>(null);
const outputBatchSelections = ref<PreviewBatchSelection[]>([]);
const committingBatch = ref(false);
const commitError = ref<string | null>(null);
const removingBatchId = ref<string | null>(null);
const replantingBatchId = ref<string | null>(null);
const replacementBatchId = ref<string | null>(null);
const replacementInput = ref<PlantPreviewInputInfo | null>(null);
const plantingEditorRevision = ref(0);
const submittingPlantPreview = ref(false);
const plantingActive = computed(
  () =>
    submittingPlantPreview.value ||
    ["queued", "running", "cancel_requested"].includes(plantPreview.value?.status ?? ""),
);
onBeforeRouteLeave(() => !submittingPlantPreview.value);
const outputSelection = useOutputAssetSelection(() => workspace.project.value?.sessionId, () => plantPreview.value);
const client = new LocalApiClient(platformBridge());
const currentSceneRevisionId = computed(
  () =>
    workspace.summary.value?.scene_revision_id ??
    workspace.preparation.value?.scene_revision_id ??
    null,
);
const usesDirectRegionSource = computed(
  () => workspace.summary.value?.scene_importer_id === "verdflow.geospatial",
);
const currentCommittedBatch = computed(() => {
  const executionId = plantPreview.value?.execution_id;
  if (!executionId) return null;
  return workspace.composition.value?.batches.find(
    (batch) => batch.plant_run_execution_id === executionId,
  ) ?? null;
});
const replacementBatch = computed(() =>
  workspace.composition.value?.batches.find(
    (batch) => batch.batch_id === replacementBatchId.value,
  ) ?? null,
);
const plantingInitialInput = computed(
  () => replacementInput.value ?? workspace.plantInput.value,
);
const effectiveSelectedAssetIds = computed(
  () => currentCommittedBatch.value
    ? currentCommittedBatch.value.selected_asset_ids
    : outputSelection.selected.value,
);
const selectedOutputPointCount = computed(() =>
  outputBatchSelections.value.reduce(
    (total, selection) => total + selection.pointCount,
    0,
  ),
);
const taskDrawerOpen = ref(false);
let restoredPlantInputId: string | null = null;

const activeTasks = computed(() =>
  workspace.tasks.value.filter((task) =>
    ["queued", "running", "awaiting_user", "cancelling"].includes(task.status),
  ),
);

const visibleTasks = computed(() =>
  taskDrawerOpen.value ? workspace.tasks.value : activeTasks.value,
);

const stages = computed(() => {
  const preparation = workspace.preparation.value;
  const hasScene = Boolean(preparation?.scene_revision_id);
  const hasRegions = Boolean(workspace.summary.value?.region_set_id);
  const plantingStages = [
    { key: "regions", label: "区域选择", icon: MapPinned, enabled: hasRegions },
    {
      key: "planting",
      label: "植被种植",
      icon: Trees,
      enabled: hasRegions && (selectedRegionIds.value.length > 0 || plantingActive.value),
    },
    {
      key: "review",
      label: "点位预览",
      icon: Layers3,
      enabled: Boolean(
        (plantPreview.value?.generated_point_set_id &&
          plantPreview.value.asset_assignment_set_id) ||
          workspace.composition.value?.batches.length,
      ),
    },
    {
      key: "output",
      label: "输出",
      icon: Upload,
      enabled:
        (selectedOutputPointCount.value > 0 &&
          Boolean(workspace.composition.value?.batches.length)) ||
        workspace.xmlOutputs.value.length > 0,
    },
  ] satisfies Array<{
    key: WorkspaceStage;
    label: string;
    icon: typeof FileSearch;
    enabled: boolean;
  }>;
  if (usesDirectRegionSource.value) return plantingStages;
  return [
    { key: "model", label: "模型", icon: FileSearch, enabled: true },
    { key: "green", label: "绿地提取", icon: Sprout, enabled: hasScene },
    ...plantingStages,
  ] satisfies Array<{
    key: WorkspaceStage;
    label: string;
    icon: typeof FileSearch;
    enabled: boolean;
  }>;
});

watch(
  () => [
    workspace.preparation.value?.status,
    workspace.preparation.value?.region_set_id,
  ],
  ([status, regionSetId], previous) => {
    if (previous && status === previous[0] && regionSetId === previous[1]) return;
    if (
      plantPreview.value &&
      plantPreview.value.execution_id === workspace.summary.value?.plant_run_execution_id
    ) return;
    if (status === "awaiting_confirmation") activeStage.value = "green";
    else if (status === "succeeded" && regionSetId) activeStage.value = "regions";
    else activeStage.value = "model";
  },
);

watch(
  () => workspace.summary.value?.region_set_id,
  (regionSetId, previous) => {
    if (
      plantPreview.value &&
      plantPreview.value.execution_id === workspace.summary.value?.plant_run_execution_id
    ) return;
    if (
      regionSetId &&
      (!workspace.preparation.value ||
        (previous !== undefined && regionSetId !== previous))
    ) {
      activeStage.value = "regions";
    }
  },
  { immediate: true },
);

watch(
  () => workspace.plantRun.value,
  (execution, previous) => {
    if (!execution) {
      plantPreview.value = null;
      restoredPlantInputId = null;
      return;
    }
    plantPreview.value = execution;
    if (submittingPlantPreview.value) return;
    if (restoredPlantInputId === execution.execution_id) {
      if (
        activeStage.value === "planting" &&
        execution.stage === "review" &&
        execution.status === "awaiting_confirmation" &&
        previous?.status !== "awaiting_confirmation" &&
        execution.generated_point_set_id &&
        execution.asset_assignment_set_id
      ) {
        handlePreviewReady(execution);
      }
      return;
    }
    restoredPlantInputId = execution.execution_id;
    activeStage.value =
      execution.generated_point_set_id && execution.asset_assignment_set_id
        ? "review"
        : "planting";
  },
  { immediate: true },
);

watch(
  () => workspace.plantInput.value,
  (input) => {
    if (replacementInput.value) return;
    if (!input) return;
    selectedRegionIds.value = [...input.selected_region_ids];
  },
  { immediate: true },
);

const filteredMaterials = computed(() => {
  const query = searchQuery.value.trim().toLocaleLowerCase();
  const materials = workspace.materialFacts.value?.materials ?? [];
  return query
    ? materials.filter((item) =>
        [
          item.material_name,
          ...item.object_names,
          ...item.textures.flatMap((texture) => [
            texture.image_name ?? "",
            texture.source_relative_path ?? "",
          ]),
        ].some((value) => value.toLocaleLowerCase().includes(query)),
      )
    : materials;
});

const candidateByName = computed(
  () =>
    new Map(
      workspace.candidate.value?.materials.map((item) => [
        item.material_name,
        item,
      ]) ?? [],
    ),
);

const preparationMessage = computed(() => {
  const currentMaterialFlow = workspace.materialFlow.value;
  if (
    workspace.summary.value?.execution_kind === "material_flow" &&
    currentMaterialFlow
  ) {
    if (currentMaterialFlow.status === "succeeded") {
      return "新的绿地区域已提取，可以重新选择区域";
    }
    if (currentMaterialFlow.status === "failed") {
      return currentMaterialFlow.error_message || "重新提取绿地区域失败";
    }
    return workspace.activeTask.value?.business_name || "正在重新提取绿地区域";
  }
  const execution = workspace.preparation.value;
  if (!execution) {
    const plantRun = workspace.plantRun.value;
    if (plantRun?.status === "succeeded") return "种植批次与高程数据已完成";
    if (plantRun?.status === "failed") {
      return plantRun.error_message || "种植输出失败";
    }
    if (plantRun?.status === "awaiting_confirmation") {
      return "点位已生成，等待预览确认";
    }
    if (plantRun) return "正在生成种植数据";
    if (
      workspace.summary.value?.scene_revision_id &&
      workspace.summary.value.region_set_id
    ) {
      return "区域数据已就绪，可以进入区域选择";
    }
    return workspace.summary.value?.state === "new"
      ? "尚未提交模型处理"
      : "正在读取模型处理状态";
  }
  if (execution.status === "awaiting_confirmation") {
    return "材质分析已完成，等待确认绿地分类";
  }
  if (execution.status === "succeeded") {
    return "绿地区域已提取，可以进入区域选择";
  }
  if (execution.status === "failed") {
    return execution.error_message || "模型处理失败";
  }
  if (execution.status === "cancelled") return "模型处理已取消";
  return workspace.activeTask.value?.business_name || "模型正在后台处理中";
});

const businessStateLabel = computed(
  () =>
    ({
      new: "待处理",
      running: "进行中",
      awaiting_material_confirmation: "等待确认",
      ready_to_plant: "可种植",
      awaiting_point_review: "待点位确认",
      completed: "已完成",
      failed: "失败",
      cancelled: "已取消",
    })[workspace.summary.value?.state ?? "new"],
);

const statusTone = computed(() => {
  const state = workspace.summary.value?.state;
  if (state === "failed" || state === "cancelled") return "danger";
  if (
    state === "awaiting_material_confirmation" ||
    state === "awaiting_point_review"
  ) {
    return "warning";
  }
  if (state === "ready_to_plant" || state === "completed") return "success";
  return "info";
});

const selectedCounts = computed(() => {
  const counts: Record<MaterialClassification, number> = {
    general_green: 0,
    street_tree_green: 0,
    not_green: 0,
  };
  Object.values(workspace.classifications.value).forEach((value) => {
    counts[value] += 1;
  });
  return counts;
});

const isRecoveringMaterialFailure = computed(
  () => workspace.preparation.value?.status === "failed",
);

const classificationOptions: ReadonlyArray<{
  value: MaterialClassification;
  label: string;
}> = [
  { value: "general_green", label: "普通绿地" },
  { value: "street_tree_green", label: "行道树绿地" },
  { value: "not_green", label: "非绿地" },
];

const updateClassification = (materialName: string, event: Event) => {
  const value = (event.target as HTMLSelectElement)
    .value as MaterialClassification;
  workspace.setClassification(materialName, value);
};

const submitMaterialSelection = async () => {
  if (workspace.preparation.value?.status === "succeeded") {
    const confirmed = await confirmAction({
      title: "重新提取绿地？",
      message: "当前选区、全部已保存种植批次与输出将被放弃，且无法恢复。",
      confirmLabel: "重新提取",
      tone: "danger",
    });
    if (!confirmed) return;
  }
  await workspace.confirmMaterials();
};

const handlePreviewReady = (execution: PlantRunExecutionInfo) => {
  plantPreview.value = execution;
  outputBatchSelections.value = [];
  activeStage.value = "review";
};

const handlePreviewSubmitted = (
  execution: PlantRunExecutionInfo,
  input: PlantPreviewInputInfo | null,
) => {
  if (replacementInput.value && input) replacementInput.value = input;
  outputBatchSelections.value = [];
  workspace.acceptPlantPreview(execution, input);
};

const handleSubmittingChanged = (submitting: boolean) => {
  submittingPlantPreview.value = submitting;
  if (!submitting) void workspace.refresh();
};

const activateStage = (stage: WorkspaceStage) => {
  if (submittingPlantPreview.value) return;
  if (
    replacementBatchId.value &&
    !plantingActive.value &&
    stage !== "planting" &&
    !(stage === "review" && plantPreview.value)
  ) {
    cancelBatchReplacement();
  }
  if (stage === "planting" && plantingActive.value && workspace.plantInput.value) {
    selectedRegionIds.value = [...workspace.plantInput.value.selected_region_ids];
  }
  activeStage.value = stage;
};

const clearCurrentPlantRun = () => {
  plantPreview.value = null;
  workspace.plantRun.value = null;
  workspace.plantInput.value = null;
  selectedRegionIds.value = [];
  restoredPlantInputId = null;
  replacementBatchId.value = null;
  replacementInput.value = null;
};

const cancelBatchReplacement = () => {
  replacementBatchId.value = null;
  replacementInput.value = null;
  selectedRegionIds.value = [];
};

const leavePlantingConfiguration = () => {
  if (replacementBatchId.value) {
    cancelBatchReplacement();
    activeStage.value = "review";
    return;
  }
  activeStage.value = "regions";
};

const ACTIVE_BATCH_OUTPUT_STATUSES = new Set([
  "queued",
  "running",
  "cancel_requested",
]);

const stopBatchOutput = async (batchId: string) => {
  const sessionId = workspace.project.value?.sessionId;
  const batch = workspace.composition.value?.batches.find(
    (item) => item.batch_id === batchId,
  );
  if (!sessionId || !batch) return;

  let execution = await client.plantPreview(
    sessionId,
    batch.plant_run_execution_id,
  );
  if (!ACTIVE_BATCH_OUTPUT_STATUSES.has(execution.status)) return;

  if (execution.status !== "cancel_requested") {
    await client.cancelPlantRun(sessionId, execution.execution_id);
  }

  const deadline = Date.now() + 30_000;
  while (
    ACTIVE_BATCH_OUTPUT_STATUSES.has(execution.status) &&
    Date.now() < deadline
  ) {
    await new Promise((resolve) => window.setTimeout(resolve, 400));
    execution = await client.plantPreview(sessionId, execution.execution_id);
  }
  await workspace.refresh();
  if (ACTIVE_BATCH_OUTPUT_STATUSES.has(execution.status)) {
    throw new Error("该批次的后台输出仍在结束，请稍后重试");
  }
};

const stopReplacementBatchOutput = async () => {
  if (!replacementBatchId.value) return;
  await stopBatchOutput(replacementBatchId.value);
};

const saveCurrentBatchAndContinue = async (
  selectedAssetIds: string[] | null,
) => {
  const sessionId = workspace.project.value?.sessionId;
  const execution = plantPreview.value;
  if (!sessionId || committingBatch.value) return;
  if (!execution) {
    outputBatchSelections.value = [];
    selectedRegionIds.value = [];
    activeStage.value = "regions";
    return;
  }
  committingBatch.value = true;
  commitError.value = null;
  try {
    await stopReplacementBatchOutput();
    workspace.composition.value = await client.commitPlantingBatch({
      sessionId,
      executionId: execution.execution_id,
      selectedAssetIds:
        currentCommittedBatch.value
          ? currentCommittedBatch.value.selected_asset_ids
          : selectedAssetIds,
      continuePlanting: true,
      replacingBatchId: replacementBatchId.value,
    });
    clearCurrentPlantRun();
    outputBatchSelections.value = [];
    await workspace.refresh();
    activeStage.value = "regions";
  } catch (error) {
    commitError.value = error instanceof Error ? error.message : "保存种植批次失败";
  } finally {
    committingBatch.value = false;
  }
};

const outputPreviewPoints = async (selections: PreviewBatchSelection[]) => {
  const sessionId = workspace.project.value?.sessionId;
  if (!sessionId || committingBatch.value) return;
  const retained = selections.filter((selection) => selection.pointCount > 0);
  if (!retained.length) {
    commitError.value = "请至少保留一个种植批次中的一种植被";
    return;
  }
  committingBatch.value = true;
  commitError.value = null;
  try {
    let composition = workspace.composition.value;
    const execution = plantPreview.value;
    const draftSelection = execution
      ? selections.find(
          (selection) =>
            selection.batchId === null &&
            selection.plantRunExecutionId === execution.execution_id,
        )
      : undefined;
    if (execution && draftSelection?.pointCount === 0) {
      const response = await client.discardPlantPreview({
        sessionId,
        executionId: execution.execution_id,
      });
      composition = response.composition;
      workspace.composition.value = composition;
    } else if (execution) {
      await stopReplacementBatchOutput();
      composition = await client.commitPlantingBatch({
        sessionId,
        executionId: execution.execution_id,
        selectedAssetIds:
          currentCommittedBatch.value
            ? currentCommittedBatch.value.selected_asset_ids
            : draftSelection?.selectedAssetIds ?? effectiveSelectedAssetIds.value,
        continuePlanting: true,
        replacingBatchId: replacementBatchId.value,
      });
      workspace.composition.value = composition;
    }
    if (!composition) throw new Error("当前没有可输出的种植批次");
    const nextSelections = selections.flatMap((selection) => {
      if (selection.batchId) return selection;
      if (selection.pointCount === 0) return [];
      const committed = composition?.batches.find(
        (batch) =>
          batch.plant_run_execution_id === selection.plantRunExecutionId,
      );
      if (!committed) throw new Error("当前种植批次未能进入项目组合");
      return [{ ...selection, batchId: committed.batch_id }];
    });
    clearCurrentPlantRun();
    outputBatchSelections.value = nextSelections;
    await workspace.refresh();
    activeStage.value = "output";
  } catch (error) {
    commitError.value = error instanceof Error ? error.message : "准备组合输出失败";
  } finally {
    committingBatch.value = false;
  }
};

const replantBatch = async (batchId: string) => {
  const sessionId = workspace.project.value?.sessionId;
  const batch = workspace.composition.value?.batches.find(
    (item) => item.batch_id === batchId,
  );
  if (
    !sessionId ||
    !batch ||
    committingBatch.value ||
    removingBatchId.value ||
    replantingBatchId.value ||
    plantingActive.value
  ) return;
  replantingBatchId.value = batchId;
  commitError.value = null;
  try {
    const input = await client.plantPreviewInput(
      sessionId,
      batch.plant_run_execution_id,
    );
    if (
      input.scene_revision_id !== currentSceneRevisionId.value ||
      input.region_set_id !== workspace.summary.value?.region_set_id
    ) {
      throw new Error("该批次不属于项目当前的场景与区域数据");
    }
    replacementBatchId.value = batchId;
    replacementInput.value = input;
    selectedRegionIds.value = [...input.selected_region_ids];
    plantPreview.value = null;
    outputBatchSelections.value = [];
    plantingEditorRevision.value += 1;
    activeStage.value = "planting";
  } catch (error) {
    commitError.value = error instanceof Error ? error.message : "读取批次配置失败";
  } finally {
    replantingBatchId.value = null;
  }
};

const removeBatch = async (batchId: string) => {
  const sessionId = workspace.project.value?.sessionId;
  if (!sessionId || removingBatchId.value) return;
  const batch = workspace.composition.value?.batches.find(
    (item) => item.batch_id === batchId,
  );
  const confirmed = await confirmAction({
    title: `删除第 ${batch ? batch.ordinal + 1 : "?"} 批种植？`,
    message:
      batch && ACTIVE_BATCH_OUTPUT_STATUSES.has(batch.status)
        ? "该批次仍在统一输出中采样高程。继续会先停止采样，再移除批次并释放对应区域。此操作无法恢复。"
        : "该批次将从当前组合中移除，对应区域会重新允许选择。此操作无法恢复。",
    confirmLabel: "删除批次",
    tone: "danger",
  });
  if (!confirmed) return;
  removingBatchId.value = batchId;
  commitError.value = null;
  try {
    await stopBatchOutput(batchId);
    const response = await client.removePlantingBatch({ sessionId, batchId });
    workspace.composition.value = response.composition;
    if (
      batch &&
      batch.plant_run_execution_id === plantPreview.value?.execution_id
    ) {
      clearCurrentPlantRun();
    }
    outputBatchSelections.value = outputBatchSelections.value.filter(
      (selection) => selection.batchId !== batchId,
    );
    await workspace.refresh();
    if (!response.composition?.batches.length && !plantPreview.value) {
      activeStage.value = "regions";
    }
  } catch (error) {
    commitError.value = error instanceof Error ? error.message : "删除种植批次失败";
  } finally {
    removingBatchId.value = null;
  }
};

const textureLabel = (material: MaterialFactItem) => {
  const texture = material.textures.find(
    (item) => item.source_relative_path || item.image_name,
  );
  return texture?.source_relative_path || texture?.image_name || "无纹理路径";
};

const formatTime = (value: string | null) => {
  if (!value) return "尚未上报";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
};

const taskStatusLabel = (task: ProjectTaskInfo) =>
  ({
    queued: "等待执行",
    running: "执行中",
    awaiting_user: "等待用户",
    cancelling: "正在取消",
    cancelled: "已取消",
    succeeded: "已完成",
    failed: "失败",
    interrupted: "已中断",
  })[task.status];
</script>

<template>
  <section class="dts-workspace">
    <header class="workspace-header">
      <button type="button" title="返回 DTS" :disabled="submittingPlantPreview" @click="router.push('/dts')">
        <ArrowLeft :size="18" />
      </button>
      <div>
        <span>DTS 项目</span>
        <h1>{{ workspace.project.value?.name || "正在读取项目" }}</h1>
      </div>
      <button
        class="secondary-button"
        type="button"
        :disabled="workspace.refreshing.value"
        @click="workspace.refresh({ refreshRegistry: true })"
      >
        <RefreshCw
          :size="17"
          :class="{ spinning: workspace.refreshing.value }"
        />
        刷新状态
      </button>
    </header>

    <div v-if="workspace.errorMessage.value" class="inline-alert" role="alert">
      <div>
        <strong>工作台暂时无法更新</strong>
        <p>{{ workspace.errorMessage.value }}</p>
      </div>
      <button type="button" @click="workspace.refresh({ refreshRegistry: true })">
        重试
      </button>
    </div>

    <div
      v-else-if="workspace.materialFactsWarning.value"
      class="inline-alert inline-alert--warning"
      role="status"
    >
      <div>
        <strong>部分历史详情不可用</strong>
        <p>{{ workspace.materialFactsWarning.value }}</p>
      </div>
    </div>

    <div v-if="workspace.project.value" class="dts-workbench">
      <aside class="workbench-rail">
        <nav aria-label="DTS 种植流程">
          <button
            v-for="(stage, index) in stages"
            :key="stage.key"
            type="button"
            :class="{ active: activeStage === stage.key }"
            :disabled="!stage.enabled || submittingPlantPreview"
            @click="activateStage(stage.key)"
          >
            <span>{{ index + 1 }}</span>
            <component :is="stage.icon" :size="17" />
            {{ stage.label }}
          </button>
        </nav>

        <section class="workbench-status" :data-tone="statusTone">
          <span>当前状态</span>
          <strong>{{ businessStateLabel }}</strong>
          <p>{{ preparationMessage }}</p>
        </section>

        <details
          class="task-drawer"
          :open="taskDrawerOpen"
          @toggle="taskDrawerOpen = ($event.currentTarget as HTMLDetailsElement).open"
        >
          <summary>
            <span>项目任务</span>
            <small v-if="activeTasks.length">{{ activeTasks.length }}</small>
            <small v-else>{{ workspace.tasks.value.length }}</small>
          </summary>
          <div v-if="visibleTasks.length" class="task-list">
            <article
              v-for="task in visibleTasks"
              :key="task.job_id"
              class="task-row"
            >
              <header>
                <strong>{{ task.business_name }}</strong>
                <span :data-status="task.status">{{ taskStatusLabel(task) }}</span>
              </header>
              <div class="task-progress" aria-hidden="true">
                <i :style="{ width: `${Math.max(0, Math.min(1, task.progress)) * 100}%` }" />
              </div>
              <p v-if="task.error_message">{{ task.error_message }}</p>
              <small v-else>
                心跳 {{ formatTime(task.heartbeat_at) }}
                <template v-if="task.attempt_count > 1">
                  · 第 {{ task.attempt_count }} 次尝试
                </template>
              </small>
            </article>
          </div>
          <p v-else class="task-empty">暂无后台任务</p>
        </details>

        <div v-if="!taskDrawerOpen && activeTasks.length" class="task-list task-list--active">
          <article v-for="task in activeTasks" :key="task.job_id" class="task-row">
            <header>
              <strong>{{ task.business_name }}</strong>
              <span :data-status="task.status">{{ taskStatusLabel(task) }}</span>
            </header>
            <div class="task-progress" aria-hidden="true">
              <i :style="{ width: `${Math.max(0, Math.min(1, task.progress)) * 100}%` }" />
            </div>
            <small>心跳 {{ formatTime(task.heartbeat_at) }}</small>
          </article>
        </div>
      </aside>

      <main class="workbench-main">
        <PlantingTaskStatus
          v-if="plantPreview && ['queued', 'running', 'cancel_requested'].includes(plantPreview.status)"
          :key="plantPreview.execution_id"
          :execution="plantPreview"
          :cancelling="workspace.cancellingPlantPreview.value"
          :cancellation-error="workspace.plantCancellationError.value"
          @cancel="workspace.cancelPlantPreview"
        />
        <div v-if="workspace.loading.value" class="workspace-loading">
          <RefreshCw :size="24" class="spinning" />
          <strong>正在读取项目状态</strong>
        </div>

        <RegionStage
          v-else-if="
            activeStage === 'regions' &&
            workspace.project.value.sessionId &&
            workspace.summary.value?.region_set_id
          "
          :key="`${workspace.project.value.sessionId}:${workspace.summary.value.region_set_id}`"
          :session-id="workspace.project.value.sessionId"
          :region-set-id="workspace.summary.value.region_set_id"
          :initial-selected-region-ids="selectedRegionIds"
          :composition="workspace.composition.value"
          :operation-error="commitError"
          @selection-changed="selectedRegionIds = $event"
          @continue="activeStage = 'planting'"
        />

        <PlantingStage
          v-else-if="
            activeStage === 'planting' &&
            workspace.project.value.sessionId &&
            currentSceneRevisionId &&
            workspace.summary.value?.region_set_id &&
            (!plantPreview || plantingInitialInput)
          "
          :key="`planting:${workspace.project.value.sessionId}:${workspace.summary.value.region_set_id}:${plantingEditorRevision}`"
          :session-id="workspace.project.value.sessionId"
          :scene-revision-id="currentSceneRevisionId"
          :region-set-id="workspace.summary.value.region_set_id"
          :selected-region-ids="selectedRegionIds"
          :initial-input="plantingInitialInput"
          :current-execution="plantPreview"
          :replacing-batch-ordinal="replacementBatch?.ordinal ?? null"
          @back="leavePlantingConfiguration"
          @preview-submitted="handlePreviewSubmitted"
          @submitting-changed="handleSubmittingChanged"
        />

        <div
          v-else-if="activeStage === 'planting' && plantPreview && !plantingInitialInput"
          class="workspace-loading"
          role="status"
        >
          <RefreshCw v-if="!workspace.errorMessage.value" :size="24" class="spinning" />
          <strong>{{ workspace.errorMessage.value ? '种植配置暂未恢复' : '正在恢复种植配置' }}</strong>
        </div>

        <template
          v-else-if="
            activeStage === 'green' &&
            workspace.materialFacts.value
          "
        >
          <header class="material-header">
            <div>
              <span>绿地提取</span>
              <h2>
                {{
                  workspace.preparation.value?.status === "succeeded"
                    ? "调整材质分类"
                    : isRecoveringMaterialFailure
                      ? "恢复绿地提取"
                      : "确认材质分类"
                }}
              </h2>
              <p>
                {{
                  isRecoveringMaterialFailure
                    ? "上次提取未完成。确认当前分类后，将复用事实报告重新执行区域提取。"
                    : "保存后 Core 将复用当前事实报告，并按完整分类重新提取绿地区域。"
                }}
              </p>
            </div>
            <div class="material-summary" aria-label="分类统计">
              <span>普通绿地 <strong>{{ selectedCounts.general_green }}</strong></span>
              <span>行道树 <strong>{{ selectedCounts.street_tree_green }}</strong></span>
              <span>非绿地 <strong>{{ selectedCounts.not_green }}</strong></span>
            </div>
          </header>

          <div v-if="workspace.candidate.value?.warnings.length" class="material-warning">
            <CircleAlert :size="17" />
            <span>{{ workspace.candidate.value.warnings.join("；") }}</span>
          </div>

          <div class="material-toolbar">
            <label>
              <Search :size="16" />
              <input v-model="searchQuery" type="search" placeholder="搜索材质、对象或纹理路径" />
            </label>
            <span>共 {{ workspace.materialFacts.value?.materials.length || 0 }} 个材质</span>
          </div>

          <div class="material-table-wrap">
            <table class="material-table">
              <thead>
                <tr>
                  <th>材质事实</th>
                  <th>规模</th>
                  <th>分析依据</th>
                  <th>分类</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="material in filteredMaterials" :key="material.material_name">
                  <td>
                    <strong>{{ material.material_name }}</strong>
                    <small :title="textureLabel(material)">{{ textureLabel(material) }}</small>
                  </td>
                  <td>
                    <span>{{ material.face_count.toLocaleString() }} 面</span>
                    <small>{{ material.object_count }} 个对象</small>
                  </td>
                  <td>
                    <template v-if="candidateByName.get(material.material_name)">
                      <span>
                        置信度
                        {{ Math.round((candidateByName.get(material.material_name)?.confidence || 0) * 100) }}%
                      </span>
                      <small :title="candidateByName.get(material.material_name)?.evidence.join('；')">
                        {{ candidateByName.get(material.material_name)?.evidence.join("；") || "无补充证据" }}
                      </small>
                    </template>
                    <template v-else>
                      <span>人工选择</span>
                      <small>未使用模型候选</small>
                    </template>
                  </td>
                  <td>
                    <select
                      :value="workspace.classifications.value[material.material_name]"
                      :disabled="workspace.materialFlowActive.value"
                      :aria-label="`${material.material_name} 分类`"
                      @change="updateClassification(material.material_name, $event)"
                    >
                      <option
                        v-for="option in classificationOptions"
                        :key="option.value"
                        :value="option.value"
                      >
                        {{ option.label }}
                      </option>
                    </select>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <footer class="material-actions">
            <span>
              {{
                workspace.materialFlowActive.value
                  ? "绿地区域正在后台提取，完成前不可重复提交。"
                  : "确认后将继续执行绿地区域提取。"
              }}
            </span>
            <button
              class="primary-button"
              type="button"
              :disabled="
                workspace.confirming.value ||
                workspace.materialFlowActive.value ||
                !workspace.materialFacts.value
              "
              @click="submitMaterialSelection"
            >
              <Clock3 v-if="workspace.materialFlowActive.value" :size="17" />
              <Check v-else :size="17" />
              {{
                workspace.confirming.value
                  ? "正在提交"
                  : workspace.materialFlowActive.value
                    ? "正在提取绿地"
                  : workspace.preparation.value?.status === "succeeded"
                    ? "重新提取绿地"
                    : isRecoveringMaterialFailure
                      ? "重试绿地提取"
                    : "确认并提取绿地"
              }}
            </button>
          </footer>
        </template>

        <div v-else-if="activeStage === 'model'" class="stage-overview">
          <div class="stage-overview__icon" :data-tone="statusTone">
            <Check v-if="workspace.preparation.value?.status === 'succeeded'" :size="26" />
            <CircleAlert v-else-if="workspace.preparation.value?.status === 'failed'" :size="26" />
            <Clock3 v-else :size="26" />
          </div>
          <span>模型</span>
          <h2>{{ preparationMessage }}</h2>
          <p v-if="workspace.activeTask.value">
            {{ workspace.activeTask.value.business_name }}，进度
            {{ Math.round(workspace.activeTask.value.progress * 100) }}%。
          </p>
          <p v-else-if="workspace.preparation.value?.region_set_id">
            RegionSet 已就绪：{{ workspace.preparation.value.region_set_id }}
          </p>
          <p v-else>VerdFlow 会在后台推进当前阶段，页面只读取持久执行状态。</p>
        </div>

        <div v-else-if="activeStage === 'green'" class="stage-overview">
          <div class="stage-overview__icon" :data-tone="workspace.preparation.value?.status === 'failed' ? 'danger' : 'success'">
            <CircleAlert v-if="workspace.preparation.value?.status === 'failed'" :size="26" />
            <Check v-else :size="26" />
          </div>
          <span>绿地提取</span>
          <h2>{{ workspace.preparation.value?.status === 'failed' ? '绿地提取失败' : '绿地材质已确认' }}</h2>
          <p>{{ workspace.preparation.value?.status === 'failed' ? '事实报告仍然可用，请刷新状态后重试提取。' : '当前 RegionSet 已由确认后的精确材质规则生成。' }}</p>
        </div>

        <PointReviewStage
          v-else-if="
            activeStage === 'review' &&
            workspace.project.value.sessionId &&
            ((plantPreview?.generated_point_set_id &&
              plantPreview.asset_assignment_set_id) ||
              workspace.composition.value?.batches.length)
          "
          :key="`review:${workspace.project.value.sessionId}:${plantPreview?.generated_point_set_id ?? 'composition'}:${workspace.composition.value?.generation ?? 0}`"
          :session-id="workspace.project.value.sessionId"
          :current-execution-id="plantPreview?.execution_id"
          :point-set-id="plantPreview?.generated_point_set_id"
          :assignment-set-id="plantPreview?.asset_assignment_set_id"
          :current-region-count="workspace.plantInput.value?.selected_region_ids.length"
          :current-selected-region-ids="workspace.plantInput.value?.selected_region_ids"
          :current-selected-asset-ids="effectiveSelectedAssetIds"
          :selection-loading="outputSelection.loading.value"
          :selection-error="outputSelection.error.value"
          :committed-batches="workspace.composition.value?.batches ?? []"
          :initial-selections="outputBatchSelections"
          :committing="committingBatch"
          :commit-error="commitError"
          :removing-batch-id="removingBatchId"
          :replanting-batch-id="replantingBatchId"
          :replacing-batch-id="replacementBatchId"
          @update:current-selected-asset-ids="outputSelection.update"
          @back="activeStage = 'planting'"
          @continue-planting="saveCurrentBatchAndContinue"
          @output-preview="outputPreviewPoints"
          @remove-batch="removeBatch"
          @replant-batch="replantBatch"
        />

        <CompositionOutputStage
          v-else-if="
            activeStage === 'output' &&
            workspace.project.value.sessionId &&
            ((workspace.composition.value && outputBatchSelections.length) ||
              workspace.xmlOutputs.value.length)
          "
          :key="`composition-output:${workspace.project.value.sessionId}:${workspace.composition.value?.generation ?? 0}`"
          :session-id="workspace.project.value.sessionId"
          :composition="workspace.composition.value"
          :batch-selections="outputBatchSelections"
          :selected-point-count="selectedOutputPointCount"
          @back="activeStage = workspace.composition.value?.batches.length ? 'review' : 'regions'"
          @outputs-changed="workspace.xmlOutputs.value = $event"
        />

        <div v-else class="stage-overview">
          <div class="stage-overview__icon">
            <Clock3 :size="26" />
          </div>
          <span>{{ stages.find((stage) => stage.key === activeStage)?.label }}</span>
          <h2>该阶段将在下一条纵向切片接入</h2>
          <p>当前选区会保留在工作台内，用于后续策略和资产映射配置。</p>
        </div>
      </main>
    </div>

    <div v-else-if="!workspace.loading.value" class="inline-alert" role="alert">
      <div>
        <strong>没有找到该项目</strong>
        <p>项目可能尚未进入最近项目 Registry，或本地服务暂时不可用。</p>
      </div>
      <button type="button" @click="router.push('/projects')">返回项目</button>
    </div>
  </section>
</template>
