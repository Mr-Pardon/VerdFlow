<script setup lang="ts">
import { Check, FileDown, FolderOpen, LoaderCircle, Trash2 } from "@lucide/vue";
import {
  computed,
  onBeforeUnmount,
  onMounted,
  reactive,
  ref,
  shallowRef,
} from "vue";

import { LocalApiClient, LocalApiError } from "../../api";
import type {
  CompositionExportOperationInfo,
  ExportDownloadInfo,
  PlantingCompositionSummary,
  ProjectXmlOutputInfo,
} from "../../api";
import { confirmAction } from "../../components/confirmDialog";
import { platformBridge } from "../../platform";
import type { PreviewBatchSelection } from "./compositionOutput";

const props = defineProps<{
  sessionId: string;
  composition: PlantingCompositionSummary | null;
  batchSelections: PreviewBatchSelection[];
  selectedPointCount: number;
}>();

const emit = defineEmits<{
  back: [];
  outputsChanged: [outputs: ProjectXmlOutputInfo[]];
}>();

const client = new LocalApiClient(platformBridge());
const offset = reactive({ x: 0, y: 0, z: 0 });
const operation = shallowRef<CompositionExportOperationInfo | null>(null);
const exportInfo = shallowRef<ExportDownloadInfo | null>(null);
const outputs = ref<ProjectXmlOutputInfo[]>([]);
const submitting = ref(false);
const loadingOutputs = ref(false);
const savingExportId = ref<string | null>(null);
const deletingExportId = ref<string | null>(null);
const savedPath = ref<string | null>(null);
const errorMessage = ref<string | null>(null);
let exportPollTimer: ReturnType<typeof setTimeout> | undefined;

const retainedSelections = computed(() =>
  props.batchSelections.filter(
    (selection) => selection.batchId !== null && selection.pointCount > 0,
  ),
);
const retainedBatchIds = computed(
  () => new Set(retainedSelections.value.map((selection) => selection.batchId)),
);
const retainedBatches = computed(() =>
  (props.composition?.batches ?? []).filter((batch) =>
    retainedBatchIds.value.has(batch.batch_id),
  ),
);
const occupiedRegionCount = computed(
  () =>
    new Set(
      retainedBatches.value.flatMap((batch) => batch.selected_region_ids),
    ).size,
);
const validOffset = computed(() =>
  [offset.x, offset.y, offset.z].every(Number.isFinite),
);
const outputChanged = computed(
  () =>
    exportInfo.value !== null &&
    [offset.x, offset.y, offset.z].some(
      (value, index) => value !== exportInfo.value?.offset[index],
    ),
);
const busy = computed(
  () =>
    submitting.value ||
    ["queued", "running"].includes(operation.value?.status ?? ""),
);
const canGenerate = computed(
  () =>
    retainedSelections.value.length > 0 &&
    props.composition !== null &&
    retainedBatches.value.length === retainedSelections.value.length &&
    validOffset.value &&
    !busy.value &&
    (exportInfo.value === null || outputChanged.value),
);
const progressLabel = computed(() => {
  if (busy.value && operation.value?.stage === "elevation") {
    return "正在为预览点位采样高程";
  }
  if (busy.value) return "正在生成预览点位 DTS XML";
  if (outputChanged.value) return "输出偏移已更改，请重新生成 XML";
  if (exportInfo.value) return "预览点位已生成，可以保存到本地";
  return "所选预览点位已就绪，可以生成一份 XML";
});

const generate = async () => {
  if (!canGenerate.value) return;
  submitting.value = true;
  errorMessage.value = null;
  savedPath.value = null;
  try {
    await handleOperation(
      await client.exportPlantingComposition({
        sessionId: props.sessionId,
        offset: [offset.x, offset.y, offset.z],
        batches: retainedSelections.value.map((selection) => ({
          batch_id: selection.batchId as string,
          selected_asset_ids: selection.selectedAssetIds,
        })),
      }),
    );
  } catch (error) {
    submitting.value = false;
    errorMessage.value = readableError(error);
  }
};

const handleOperation = async (next: CompositionExportOperationInfo) => {
  operation.value = next;
  if (next.status === "succeeded" && next.export_id) {
    if (exportPollTimer) clearTimeout(exportPollTimer);
    exportPollTimer = undefined;
    setExportInfo(await client.exportInfo(props.sessionId, next.export_id));
    await loadOutputs();
    submitting.value = false;
    return;
  }
  if (next.status === "succeeded") {
    submitting.value = false;
    errorMessage.value = "XML 生成已结束，但没有找到对应的项目文件";
    return;
  }
  if (["failed", "cancelled", "interrupted"].includes(next.status)) {
    if (exportPollTimer) clearTimeout(exportPollTimer);
    exportPollTimer = undefined;
    submitting.value = false;
    errorMessage.value = next.error_message || "XML 生成失败";
    return;
  }
  scheduleExportPoll(next);
};

const scheduleExportPoll = (current: CompositionExportOperationInfo) => {
  if (exportPollTimer) clearTimeout(exportPollTimer);
  exportPollTimer = setTimeout(async () => {
    try {
      await handleOperation(
        await client.plantingCompositionExport({
          sessionId: props.sessionId,
          operationId: current.operation_id,
        }),
      );
    } catch (error) {
      submitting.value = false;
      errorMessage.value = readableError(error);
    }
  }, 1200);
};

const setExportInfo = (info: ExportDownloadInfo) => {
  exportInfo.value = info;
  [offset.x, offset.y, offset.z] = info.offset;
};

const loadOutputs = async () => {
  loadingOutputs.value = true;
  try {
    outputs.value = (await client.projectXmlOutputs(props.sessionId)).items;
    emit("outputsChanged", outputs.value);
  } catch (error) {
    errorMessage.value = readableError(error);
  } finally {
    loadingOutputs.value = false;
  }
};

const save = async (exportId: string) => {
  if (busy.value || savingExportId.value) return;
  savingExportId.value = exportId;
  errorMessage.value = null;
  try {
    const info = await client.exportInfo(props.sessionId, exportId);
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
  } catch (error) {
    errorMessage.value = readableError(error);
  } finally {
    savingExportId.value = null;
  }
};

const removeOutput = async (output: ProjectXmlOutputInfo) => {
  if (deletingExportId.value || busy.value) return;
  const confirmed = await confirmAction({
    title: "删除这份 XML？",
    message: "该文件将从项目输出列表中移除，之后无法再从 VerdFlow 另存。此操作无法恢复。",
    confirmLabel: "删除 XML",
    tone: "danger",
  });
  if (!confirmed) return;
  deletingExportId.value = output.export_id;
  errorMessage.value = null;
  try {
    await client.deleteProjectXmlOutput({
      sessionId: props.sessionId,
      exportId: output.export_id,
    });
    outputs.value = outputs.value.filter(
      (item) => item.export_id !== output.export_id,
    );
    emit("outputsChanged", outputs.value);
    if (exportInfo.value?.export_id === output.export_id) {
      exportInfo.value = null;
      operation.value = null;
    }
  } catch (error) {
    errorMessage.value = readableError(error);
  } finally {
    deletingExportId.value = null;
  }
};

const revealSavedPath = async () => {
  if (!savedPath.value) return;
  const result = await platformBridge().revealPath(savedPath.value);
  if (!result.ok && result.reason !== "cancelled") {
    errorMessage.value = result.message;
  }
};

const formatBytes = (value: number) => {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
};

const formatDate = (value: string) =>
  new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));

const initialize = async () => {
  await loadOutputs();
  try {
    const active = await client.activePlantingCompositionExport(props.sessionId);
    if (active.operation) await handleOperation(active.operation);
  } catch (error) {
    errorMessage.value = readableError(error);
  }
};

onMounted(initialize);
onBeforeUnmount(() => {
  if (exportPollTimer) clearTimeout(exportPollTimer);
});

const readableError = (error: unknown) => {
  if (error instanceof LocalApiError) return error.message;
  return error instanceof Error ? error.message : "组合输出执行失败";
};
</script>

<template>
  <section class="output-stage composition-output-stage">
    <header>
      <div>
        <span>点位输出</span>
        <h2>输出预览中保留的点位</h2>
        <p>按预览筛选重新生成 DTS XML，不修改已保存批次或拼接历史文件。</p>
      </div>
      <div class="output-status" data-status="succeeded">
        <LoaderCircle v-if="busy" :size="18" class="spinning" />
        <Check v-else :size="18" />
        <span>{{ progressLabel }}</span>
      </div>
    </header>

    <div v-if="errorMessage" class="output-error" role="alert">{{ errorMessage }}</div>

    <div class="output-content">
      <dl class="composition-output-stats">
        <div><dt>保留批次</dt><dd>{{ retainedSelections.length }}</dd></div>
        <div><dt>占用区域</dt><dd>{{ occupiedRegionCount }}</dd></div>
        <div><dt>预览点位</dt><dd>{{ selectedPointCount.toLocaleString() }}</dd></div>
      </dl>

      <section class="output-offsets">
        <span>XML 坐标偏移</span>
        <div>
          <label>X <input v-model.number="offset.x" type="number" step="any" :disabled="busy" /></label>
          <label>Y <input v-model.number="offset.y" type="number" step="any" :disabled="busy" /></label>
          <label>Z <input v-model.number="offset.z" type="number" step="any" :disabled="busy" /></label>
        </div>
        <p>该偏移只应用于本次 XML，不改变任何批次保存的点位与高程结果。</p>
      </section>

      <section v-if="savedPath" class="output-saved">
        <Check :size="17" />
        <span :title="savedPath">已保存至 {{ savedPath }}</span>
        <button type="button" @click="revealSavedPath">
          <FolderOpen :size="16" />
          打开位置
        </button>
      </section>

      <section class="project-xml-outputs">
        <header>
          <div>
            <span>项目 XML</span>
            <p>成功生成的文件会保留在项目中，直到手动删除。</p>
          </div>
          <LoaderCircle v-if="loadingOutputs" :size="17" class="spinning" />
          <strong v-else>{{ outputs.length }}</strong>
        </header>
        <div v-if="outputs.length" class="project-xml-output-list">
          <article v-for="item in outputs" :key="item.export_id">
            <FileDown :size="21" />
            <div>
              <strong>{{ item.suggested_filename }}</strong>
              <small>
                {{ formatDate(item.created_at) }} · {{ item.record_count.toLocaleString() }} 个点位 ·
                {{ formatBytes(item.byte_size) }}
              </small>
            </div>
            <button
              class="secondary-button"
              type="button"
              :disabled="busy || Boolean(savingExportId)"
              @click="save(item.export_id)"
            >
              <LoaderCircle
                v-if="savingExportId === item.export_id"
                :size="16"
                class="spinning"
              />
              <FileDown v-else :size="16" />
              另存为
            </button>
            <button
              class="icon-button"
              type="button"
              title="删除 XML"
              :disabled="busy || Boolean(deletingExportId)"
              @click="removeOutput(item)"
            >
              <LoaderCircle
                v-if="deletingExportId === item.export_id"
                :size="16"
                class="spinning"
              />
              <Trash2 v-else :size="16" />
            </button>
          </article>
        </div>
        <p v-else-if="!loadingOutputs" class="project-xml-outputs__empty">
          尚未生成项目 XML
        </p>
      </section>
    </div>

    <footer>
      <button class="secondary-button" type="button" :disabled="busy" @click="emit('back')">
        返回点位预览
      </button>
      <button class="primary-button" type="button" :disabled="!canGenerate" @click="generate">
        <LoaderCircle v-if="busy" :size="17" class="spinning" />
        <FileDown v-else :size="17" />
        {{ busy ? "正在生成" : exportInfo ? "按当前偏移重新生成" : "生成 XML" }}
      </button>
    </footer>
  </section>
</template>
