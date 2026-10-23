<script setup lang="ts">
import {
  Check,
  FileDown,
  FolderOpen,
  LoaderCircle,
  Sprout,
} from "@lucide/vue";
import { computed, onBeforeUnmount, onMounted, reactive, ref, shallowRef } from "vue";

import { LocalApiClient, LocalApiError } from "../../api";
import type {
  ExportDownloadInfo,
  PlantPreviewReexportInfo,
  PlantRunExecutionInfo,
  PlantingCompositionSummary,
} from "../../api";
import { platformBridge } from "../../platform";

const props = defineProps<{
  sessionId: string;
  execution: PlantRunExecutionInfo;
  selectedAssetIds: string[] | null;
  selectionLoading?: boolean;
  selectionError?: string | null;
}>();

const emit = defineEmits<{
  back: [];
  executionChanged: [execution: PlantRunExecutionInfo];
  batchCommitted: [composition: PlantingCompositionSummary];
  continuePlanting: [];
}>();

const client = new LocalApiClient(platformBridge());
const current = shallowRef(props.execution);
const exportInfo = shallowRef<ExportDownloadInfo | null>(null);
const offset = reactive({ x: 0, y: 0, z: 0 });
const submitting = ref(false);
const reexporting = ref(false);
const saving = ref(false);
const savedPath = ref<string | null>(null);
const errorMessage = ref<string | null>(null);
let pollTimer: ReturnType<typeof setTimeout> | undefined;
let reexportPollTimer: ReturnType<typeof setTimeout> | undefined;

const running = computed(() =>
  ["queued", "running"].includes(current.value.status),
);
const validOffset = computed(() =>
  [offset.x, offset.y, offset.z].every(Number.isFinite),
);
const busy = computed(() => running.value || submitting.value || reexporting.value);
const validSelection = computed(() => !props.selectionLoading && !props.selectionError &&
  (props.selectedAssetIds === null || props.selectedAssetIds.length > 0));
const outputChanged = computed(() => exportInfo.value !== null && (
  [offset.x, offset.y, offset.z].some((value, index) => value !== exportInfo.value?.offset[index]) ||
  JSON.stringify(props.selectedAssetIds?.slice().sort() ?? null) !==
    JSON.stringify(exportInfo.value.selected_asset_ids?.slice().sort() ?? null)
));
const canStart = computed(
  () =>
    current.value.stage === "review" &&
    current.value.status === "awaiting_confirmation" &&
    validOffset.value &&
    validSelection.value &&
    !busy.value,
);
const canReexport = computed(
  () =>
    exportInfo.value !== null &&
    validOffset.value &&
    validSelection.value &&
    !busy.value &&
    outputChanged.value,
);
const progressLabel = computed(() => {
  if (reexporting.value) return "正在按当前设置生成 DTS XML";
  if (outputChanged.value) return "输出设置已更改，请重新生成 XML";
  if (current.value.status === "failed") return current.value.error_message || "输出失败";
  if (current.value.status === "succeeded") return "XML 已生成，可以保存到本地";
  if (current.value.stage === "elevation") return "正在进行高程采样";
  if (current.value.stage === "export") return "正在生成 DTS XML";
  if (running.value) return "正在准备输出";
  return "确认偏移值后开始高程采样与 XML 生成";
});

const startExport = async () => {
  if (!canStart.value) return;
  submitting.value = true;
  errorMessage.value = null;
  try {
    emit(
      "batchCommitted",
      await client.commitPlantingBatch({
        sessionId: props.sessionId,
        executionId: current.value.execution_id,
        selectedAssetIds: props.selectedAssetIds,
        continuePlanting: false,
      }),
    );
    updateExecution(
      await client.exportPlantPreview({
        sessionId: props.sessionId,
        executionId: current.value.execution_id,
        offset: [offset.x, offset.y, offset.z],
        selectedAssetIds: props.selectedAssetIds,
      }),
    );
    schedulePoll();
  } catch (error) {
    errorMessage.value = readableError(error);
  } finally {
    submitting.value = false;
  }
};

const refresh = async () => {
  try {
    updateExecution(
      await client.plantPreview(props.sessionId, current.value.execution_id),
    );
    if (current.value.status === "succeeded") {
      setExportInfo(await client.plantPreviewExport(
        props.sessionId,
        current.value.execution_id,
      ));
    }
    schedulePoll();
  } catch (error) {
    errorMessage.value = readableError(error);
    pollTimer = setTimeout(() => void refresh(), 3000);
  }
};

const startReexport = async () => {
  if (!canReexport.value) return;
  reexporting.value = true;
  errorMessage.value = null;
  savedPath.value = null;
  try {
    await handleReexport(
      await client.reexportPlantPreview({
        sessionId: props.sessionId,
        executionId: current.value.execution_id,
        offset: [offset.x, offset.y, offset.z],
        selectedAssetIds: props.selectedAssetIds,
      }),
    );
  } catch (error) {
    reexporting.value = false;
    errorMessage.value = readableError(error);
  }
};

const handleReexport = async (operation: PlantPreviewReexportInfo) => {
  if (operation.status === "succeeded" && operation.export_id) {
    setExportInfo(
      await client.exportInfo(props.sessionId, operation.export_id),
    );
    reexporting.value = false;
    return;
  }
  if (["failed", "cancelled", "interrupted"].includes(operation.status)) {
    reexporting.value = false;
    errorMessage.value = operation.error_message || "XML 重新生成失败";
    return;
  }
  scheduleReexportPoll(operation.export_job_id);
};

const scheduleReexportPoll = (exportJobId: string) => {
  if (reexportPollTimer) clearTimeout(reexportPollTimer);
  reexportPollTimer = setTimeout(async () => {
    try {
      await handleReexport(
        await client.plantPreviewReexport({
          sessionId: props.sessionId,
          executionId: current.value.execution_id,
          exportJobId,
        }),
      );
    } catch (error) {
      reexporting.value = false;
      errorMessage.value = readableError(error);
    }
  }, 1200);
};

const setExportInfo = (info: ExportDownloadInfo) => {
  exportInfo.value = info;
  [offset.x, offset.y, offset.z] = info.offset;
};

const updateExecution = (execution: PlantRunExecutionInfo) => {
  current.value = execution;
  emit("executionChanged", execution);
};

const schedulePoll = () => {
  if (pollTimer) clearTimeout(pollTimer);
  if (running.value) pollTimer = setTimeout(() => void refresh(), 1200);
};

const save = async () => {
  const info = exportInfo.value;
  if (!info || busy.value || outputChanged.value || !validSelection.value) return;
  saving.value = true;
  errorMessage.value = null;
  try {
    const result = await platformBridge().saveExport({
      sessionId: props.sessionId,
      exportId: info.export_id,
      suggestedName: info.suggested_filename,
      mediaType: info.media_type,
    });
    if (!result.ok) {
      if (result.reason !== "cancelled") errorMessage.value = result.message;
      return;
    }
    savedPath.value = result.value;
  } finally {
    saving.value = false;
  }
};

const revealSavedPath = async () => {
  if (!savedPath.value) return;
  const result = await platformBridge().revealPath(savedPath.value);
  if (!result.ok && result.reason !== "cancelled") errorMessage.value = result.message;
};

const formatBytes = (value: number) => {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
};

onMounted(() => {
  if (running.value || current.value.status === "succeeded") void refresh();
});

onBeforeUnmount(() => {
  if (pollTimer) clearTimeout(pollTimer);
  if (reexportPollTimer) clearTimeout(reexportPollTimer);
});

const readableError = (error: unknown) => {
  if (error instanceof LocalApiError) return error.message;
  return error instanceof Error ? error.message : "输出流程执行失败";
};
</script>

<template>
  <section class="output-stage">
    <header>
      <div>
        <span>输出</span>
        <h2>高程采样与 DTS XML</h2>
        <p>偏移仅在最终输出阶段应用，预览点位仍保持原始坐标。</p>
      </div>
      <div class="output-status" :data-status="current.status">
        <LoaderCircle v-if="busy" :size="18" class="spinning" />
        <Check v-else-if="current.status === 'succeeded'" :size="18" />
        <FileDown v-else :size="18" />
        <span>{{ progressLabel }}</span>
      </div>
    </header>

    <div v-if="errorMessage" class="output-error" role="alert">{{ errorMessage }}</div>
    <div v-if="selectionError" class="output-error" role="alert">{{ selectionError }}</div>

    <div class="output-content">
      <p>输出植被：{{ selectedAssetIds === null ? '全部' : `${selectedAssetIds.length} 类` }}</p>
      <section class="output-offsets">
        <span>XML 坐标偏移</span>
        <div>
          <label>X <input v-model.number="offset.x" type="number" step="any" :disabled="busy" /></label>
          <label>Y <input v-model.number="offset.y" type="number" step="any" :disabled="busy" /></label>
          <label>Z <input v-model.number="offset.z" type="number" step="any" :disabled="busy" /></label>
        </div>
        <p v-if="exportInfo">修改偏移后只会重新生成 XML，不会重新生成点位或执行高程采样。</p>
        <p v-else>FBX 流程会先使用未偏移点位完成高程采样，再在 XML 导出时应用以上偏移。</p>
      </section>

      <section v-if="exportInfo" class="output-result">
        <FileDown :size="24" />
        <div>
          <strong>{{ exportInfo.suggested_filename }}</strong>
          <small>{{ formatBytes(exportInfo.byte_size) }} · {{ exportInfo.media_type }}</small>
        </div>
        <button class="primary-button" type="button" :disabled="saving || busy || outputChanged || !validSelection" @click="save">
          <LoaderCircle v-if="saving" :size="17" class="spinning" />
          <FileDown v-else :size="17" />
          {{ saving ? "正在保存" : "保存 XML" }}
        </button>
      </section>

      <section v-if="savedPath" class="output-saved">
        <Check :size="17" />
        <span :title="savedPath">已保存至 {{ savedPath }}</span>
        <button type="button" @click="revealSavedPath">
          <FolderOpen :size="16" />
          打开位置
        </button>
      </section>
    </div>

    <footer>
      <button class="secondary-button" type="button" :disabled="busy" @click="emit('back')">
        返回点位预览
      </button>
      <button
        v-if="exportInfo"
        class="secondary-button"
        type="button"
        :disabled="busy || outputChanged"
        @click="emit('continuePlanting')"
      >
        <Sprout :size="17" />
        继续种植其他区域
      </button>
      <button v-if="!exportInfo" class="primary-button" type="button" :disabled="!canStart" @click="startExport">
        <LoaderCircle v-if="running || submitting" :size="17" class="spinning" />
        <FileDown v-else :size="17" />
        {{ running || submitting ? "正在生成" : "生成 XML" }}
      </button>
      <button v-else class="primary-button" type="button" :disabled="!canReexport" @click="startReexport">
        <LoaderCircle v-if="reexporting" :size="17" class="spinning" />
        <FileDown v-else :size="17" />
        {{ reexporting ? "正在重新生成" : "按当前设置重新生成 XML" }}
      </button>
    </footer>
  </section>
</template>
