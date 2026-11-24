import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch, type Ref } from "vue";
import type { LiveRegion } from "../types";
import type { DtsSdkBridge } from "../sdk/DtsSdkBridge";
import { ensureActorPickActive, type ActorPick } from "../sdk/actorPicking";
import { readableError } from "../shared/presentation";

export function useActorPicking(
  sdk: DtsSdkBridge, blocked: Ref<boolean>, error: Ref<string | null>,
  append: (dataset: string, count: number, signal: AbortSignal) => Promise<void>,
) {
  const phase = ref<"idle" | "picking" | "drawing-range" | "extracting">("idle");
  const selected = shallowRef<ActorPick | null>(null);
  const range = shallowRef<LiveRegion | null>(null);
  const highlighting = ref(false);
  const canExtract = computed(() => phase.value === "picking" && Boolean(selected.value) && !highlighting.value && !blocked.value);
  let extraction: AbortController | null = null;
  let revision = 0;

  const select = async (pick: ActorPick) => {
    const version = ++revision;
    highlighting.value = true;
    selected.value = pick;
    try {
      await sdk.previewActorPick(pick);
    } catch (failure) {
      if (version !== revision) return;
      selected.value = null;
      error.value = readableError(failure);
      await sdk.previewActorPick(null).catch(() => undefined);
    } finally {
      if (version === revision) highlighting.value = false;
    }
  };

  const listen = () => {
    sdk.startActorPicking(pick => {
      if (phase.value !== "picking") return;
      if (!pick) {
        error.value = "没有拾取到场景构件，请点草地表面。";
        return;
      }
      error.value = null;
      if (selected.value?.layer === pick.layer && selected.value.actor === pick.actor) return;
      void select(pick);
    }, cancel);
  };

  const start = () => {
    if (blocked.value || phase.value !== "idle") return;
    revision++;
    error.value = null;
    try {
      listen();
      phase.value = "picking";
    } catch (failure) { error.value = readableError(failure); }
  };

  const clear = () => {
    const version = ++revision;
    sdk.stopActorPicking();
    if (phase.value === "drawing-range") cancelRangeDrawing();
    clearRange();
    selected.value = null;
    highlighting.value = false;
    phase.value = "idle";
    void sdk.previewActorPick(null).catch(failure => {
      if (version === revision) error.value = readableError(failure);
    });
  };

  function cancel() {
    if (extraction) extraction.abort();
    else if (phase.value !== "idle") clear();
  }

  const showRange = (region: LiveRegion) => {
    try { sdk.showActorRange(region); }
    catch (failure) { error.value = readableError(failure); }
  };

  function clearRange() {
    if (!range.value) return;
    range.value = null;
    const version = revision;
    void sdk.clearActorRangePreview().catch(failure => {
      if (revision === version) error.value = readableError(failure);
    });
  }

  const drawRange = () => {
    if (!canExtract.value) return;
    error.value = null;
    phase.value = "drawing-range";
    try {
      sdk.beginActorRangeDrawing(region => {
        if (phase.value !== "drawing-range") return;
        phase.value = "picking";
        if (!region) return;
        range.value = region;
        showRange(region);
      });
    } catch (failure) {
      phase.value = "picking";
      error.value = readableError(failure);
    }
  };

  const cancelRangeDrawing = () => {
    if (phase.value !== "drawing-range") return;
    phase.value = "picking";
    try { sdk.cancelActorRangeDrawing(); }
    catch (failure) { error.value = readableError(failure); }
  };

  const extract = async () => {
    const pick = selected.value;
    if (!pick || !canExtract.value) return;
    sdk.stopActorPicking(false);
    error.value = null;
    phase.value = "extracting";
    const task = extraction = new AbortController();
    try {
      const { dataset_id: dataset, region_count: count } = await sdk.extractActor(pick, task.signal, range.value?.coordinates);
      ensureActorPickActive(task.signal);
      if (!dataset || !count) throw new Error(range.value
        ? "绘制范围内没有提取到构件面，请调整范围或切换构件。"
        : "未提取到构件轮廓，请检查场景或使用绘制选区。");
      await append(dataset, count, task.signal);
      clear();
    } catch (failure) {
      if (task.signal.aborted || blocked.value) { clear(); return; }
      error.value = readableError(failure);
      try {
        listen();
        phase.value = "picking";
        void select(pick);
        if (range.value) showRange(range.value);
      } catch { clear(); }
    } finally {
      extraction = null;
    }
  };

  watch(blocked, value => { if (value && phase.value !== "idle") cancel(); });
  const leave = () => {
    if (phase.value === "idle") return;
    cancel();
    sdk.releaseActorCapture();
  };
  onMounted(() => window.addEventListener("pagehide", leave));
  onBeforeUnmount(() => {
    window.removeEventListener("pagehide", leave);
    leave();
  });
  return { phase, selected, range, highlighting, canExtract, start, cancel, extract, drawRange, cancelRangeDrawing, clearRange };
}
