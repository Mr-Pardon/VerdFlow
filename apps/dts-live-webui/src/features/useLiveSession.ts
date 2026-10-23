import { shallowRef, ref, computed, onMounted, onBeforeUnmount } from "vue";
import { type SessionSnapshot, type SelectionConfiguration, type LiveRegion } from "../types";
import { cancelOperation, sessionSnapshot, commandsAfter, bootstrapSession, beginSelection } from "../api";
import { DtsSdkBridge } from "../sdk/DtsSdkBridge";
import type { Ref } from "vue";
import { readableError, wait } from "../shared/presentation";

interface SessionCallbacks {
  initializeConfiguration: () => Promise<void>;
  onRestored: (snapshot: SessionSnapshot) => void;
  onCompleted: () => void;
  onRegionDrawn: (region: LiveRegion) => void;
  onDrawingFinished: () => void;
  onRegionClicked: (id: string) => void;
}

export function useLiveSession(errorMessage: Ref<string | null>, callbacks: SessionCallbacks) {
  const TERMINAL_STAGES = new Set(["completed", "cancelled", "superseded", "failed"]);

  const snapshot = shallowRef<SessionSnapshot | null>(null);

  const loading = ref(true);

  const sdkReady = ref(false);

  const sdkStatus = ref("正在连接 DTS Explorer");

  const submitting = ref(false);

  const samplingActivity = ref("");
  const activityStage = ref<string | null>(null);

  const cancelling = ref(false);

  let afterSequence = 0;

  let stopped = false;

  let snapshotTimer: number | undefined;

  const currentStage = computed(() => snapshot.value?.stage ?? null);

  const operationActive = computed(
    () => currentStage.value !== null && !TERMINAL_STAGES.has(currentStage.value),
  );

  const stageLabel = computed(() => {
    if (["elevation_pending", "delivery_pending"].includes(currentStage.value ?? "") &&
        activityStage.value === currentStage.value && samplingActivity.value) return samplingActivity.value;
    const labels: Record<string, string> = {
      selection_pending: "等待区域绘制",
      selection_ready: "正在生成植被点位",
      elevation_pending: "正在采样高程",
      elevation_ready: "正在整理高程结果并生成 XML",
      delivery_pending: "正在加载到 DTS",
      completed: samplingFailures.value.length ? `部分完成 · 跳过 ${samplingFailures.value.length} 个面` : "本次种植已完成",
      cancelled: "本次种植已取消",
      superseded: "本次种植已被替换",
      failed: "本次种植失败",
    };
    return labels[currentStage.value ?? ""] ?? null;
  });

  const samplingFailures = computed(() => snapshot.value?.sampling_failures ?? []);

  const cancel = async () => {
    if (!operationActive.value || cancelling.value) return;
    cancelling.value = true;
    try {
      snapshot.value = await cancelOperation();
      const warning = sdk.cancelPendingCommands();
      if (warning) errorMessage.value = warning;
    } catch (error) {
      errorMessage.value = readableError(error);
    } finally {
      cancelling.value = false;
    }
  };

  const refreshSnapshot = async () => {
    if (stopped) return;
    try {
      const previous = snapshot.value;
      const refreshed = await sessionSnapshot();
      if (stopped) return;
      snapshot.value = refreshed;
      if (snapshot.value.error) errorMessage.value = snapshot.value.error;
      if (snapshot.value.stage === "completed" && previous?.stage !== "completed") {
        callbacks.onCompleted();
      }
      if (["cancelled", "superseded", "failed"].includes(snapshot.value.stage ?? "")) sdk.cancelPendingCommands();
    } catch (error) {
      errorMessage.value = readableError(error);
    }
  };

  const pollCommands = async () => {
    while (!stopped) {
      try {
        const page = await commandsAfter(afterSequence);
        if (stopped) return;
        for (const command of page.items) {
          if (command.sequence <= afterSequence) continue;
          afterSequence = command.sequence;
          await refreshSnapshot();
          if (stopped) return;
          if (snapshot.value?.pending_request_id !== command.correlation.request_id) continue;
          try {
            await sdk.execute(command);
          } catch (error) {
            await refreshSnapshot();
            if (stopped) return;
            if (
              snapshot.value?.operation_id === command.correlation.operation_id &&
              snapshot.value?.generation_token === command.correlation.generation_token &&
              snapshot.value?.pending_request_id === command.correlation.request_id &&
              operationActive.value
            ) {
              errorMessage.value = readableError(error);
              snapshot.value = await cancelOperation();
            }
          }
        }
        await refreshSnapshot();
      } catch (error) {
        if (stopped) return;
        errorMessage.value = readableError(error);
        await wait(1200);
      }
    }
  };

  const sdk = new DtsSdkBridge({
    onReady: () => {
      sdkReady.value = true;
      sdkStatus.value = "DTS Explorer 已连接";
    },
    onActivity: (message) => { samplingActivity.value = message; activityStage.value = currentStage.value; },
    onRegionDrawn: callbacks.onRegionDrawn,
    onDrawingFinished: callbacks.onDrawingFinished,
    onRegionClicked: callbacks.onRegionClicked,
    onError: (message) => {
      errorMessage.value = message;
    },
    onCommandComplete: () => void refreshSnapshot(),
  });

  const initialize = async () => {
    loading.value = true;
    errorMessage.value = null;
    try {
      const initialSnapshot = await bootstrapSession();
      if (stopped) return;
      snapshot.value = initialSnapshot;
      await callbacks.initializeConfiguration();
      if (stopped) return;
      callbacks.onRestored(initialSnapshot);
      void pollCommands();
      void sdk.initialize().catch((error) => {
        errorMessage.value = readableError(error);
        sdkStatus.value = "DTS Explorer 未连接";
      });
    } catch (error) {
      errorMessage.value = readableError(error);
      sdkStatus.value = "DTS Explorer 未连接";
    } finally {
      loading.value = false;
    }
  };

  const startSelection = async (configuration: SelectionConfiguration, regions: LiveRegion[]) => {
    if (operationActive.value || submitting.value) return;
    submitting.value = true;
    errorMessage.value = null;
    samplingActivity.value = "";
    try {
      await beginSelection({ ...configuration, regions });
      await refreshSnapshot();
    } catch (error) { errorMessage.value = readableError(error); }
    finally { submitting.value = false; }
  };

  onMounted(() => {
    void initialize();
    snapshotTimer = window.setInterval(() => {
      if (operationActive.value) void refreshSnapshot();
    }, 1200);
  });
  onBeforeUnmount(() => {
    stopped = true;
    sdk.dispose();
    if (snapshotTimer) window.clearInterval(snapshotTimer);
  });
  return {
    sdk,
    startSelection,
    snapshot,
    loading,
    sdkReady,
    sdkStatus,
    submitting,
    samplingActivity,
    cancelling,
    currentStage,
    operationActive,
    stageLabel,
    samplingFailures,
    cancel,
    refreshSnapshot,
    pollCommands,
  };
}
