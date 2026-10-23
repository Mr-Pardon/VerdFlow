<script setup lang="ts">
import { Clock3, LoaderCircle, Square } from "@lucide/vue";
import { computed, onBeforeUnmount, onMounted, ref } from "vue";

import type { PlantRunExecutionInfo } from "../../api";

const props = defineProps<{
  execution: PlantRunExecutionInfo;
  cancelling: boolean;
  cancellationError: string | null;
}>();
const emit = defineEmits<{ cancel: [] }>();
const now = ref(Date.now());
let timer: ReturnType<typeof setInterval> | undefined;
const elapsedSeconds = computed(() => {
  const createdAt = Date.parse(props.execution.created_at);
  return Number.isFinite(createdAt)
    ? Math.max(0, Math.floor((now.value - createdAt) / 1000))
    : 0;
});
const elapsedLabel = computed(() => {
  const seconds = elapsedSeconds.value;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return `${hours ? `${hours} 小时 ` : ""}${minutes} 分 ${seconds % 60} 秒`;
});
const cancellationPending = computed(
  () => props.cancelling || props.execution.status === "cancel_requested",
);
const longRunning = computed(
  () => props.execution.status === "running" && elapsedSeconds.value >= 300,
);
const statusLabel = computed(() => {
  if (cancellationPending.value) return "正在取消种植任务";
  if (props.execution.status === "queued") return "等待生成点位";
  return ({
    generation: "正在生成点位",
    assignment: "正在分配植被",
    elevation: "正在采样高程",
    export: "正在生成 XML",
    review: "正在准备点位预览",
    completed: "正在完成种植任务",
  })[props.execution.stage];
});
onMounted(() => {
  timer = setInterval(() => { now.value = Date.now(); }, 1000);
});
onBeforeUnmount(() => { if (timer) clearInterval(timer); });
</script>

<template>
  <section class="planting-task-status" :class="{ 'is-long-running': longRunning }" aria-label="当前种植任务">
    <LoaderCircle :size="18" class="spinning" />
    <div class="planting-task-status__details">
      <strong role="status">{{ statusLabel }}</strong>
      <span class="planting-task-status__elapsed" role="timer" aria-live="off"><Clock3 :size="13" />已用时 {{ elapsedLabel }}</span>
      <p v-if="longRunning && !cancellationPending" class="planting-task-status__notice">计算耗时较长，任务仍在执行，可继续等待或取消。</p>
      <p v-if="cancellationError" class="planting-task-status__error" role="alert">{{ cancellationError }}</p>
    </div>
    <button type="button" class="secondary-button" :disabled="cancellationPending" @click="emit('cancel')">
      <Square :size="15" />{{ cancellationPending ? "正在取消" : "取消种植" }}
    </button>
  </section>
</template>

<style scoped>
.planting-task-status {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 12px;
  padding: 14px 20px;
  color: var(--color-accent);
  background: var(--color-accent-soft);
  border-bottom: 1px solid var(--color-border);
}
.planting-task-status > svg { flex-shrink: 0; }
.planting-task-status__details { flex: 1; min-width: 0; }
.planting-task-status__details > strong { display: block; font-size: 13px; }
.planting-task-status__elapsed { display: flex; align-items: center; gap: 5px; margin-top: 4px; color: var(--color-text-muted); font-size: 11px; }
.planting-task-status__details > p { margin: 5px 0 0; font-size: 12px; line-height: 1.5; overflow-wrap: anywhere; }
.planting-task-status__notice { color: #805b20; }
.planting-task-status__error { color: var(--color-danger); }
.planting-task-status.is-long-running { background: #fff9ef; }
.planting-task-status > button { flex-shrink: 0; white-space: nowrap; }
@media (max-width: 600px) {
  .planting-task-status { flex-wrap: wrap; padding: 12px; gap: 8px; }
  .planting-task-status > button { margin-left: 26px; }
}
</style>
