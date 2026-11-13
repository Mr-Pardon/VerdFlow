<script setup lang="ts">
import { ArrowLeft, Check, ChevronLeft, ChevronRight, Crosshair, Filter, Image, Info, Layers, LoaderCircle, LockKeyhole, Maximize2, RotateCcw, ScanLine, Search, SlidersHorizontal, Undo2, X } from "@lucide/vue";
import { computed, nextTick, onBeforeUnmount, ref, shallowRef, watch } from "vue";

import { LocalApiClient, LocalApiError, type Bounds3D, type ImageryCandidate, type ImageryConfirmationRefinement, type ImageryRefinement, type ImageryReviewItem } from "../../api";
import DismissibleNotice from "../../components/DismissibleNotice.vue";
import { platformBridge } from "../../platform";
import { createImageryDetail, type ImageryDetailTile } from "./imageryDetail";
import { areaOrder, decodeRegionBits, encodeRegionBits, hasRegion, markRegion } from "./imageryReviewIndex";
import { loadImageryReviewMap } from "./imageryReviewMap";
import RegionCanvas from "./RegionCanvas.vue";
import type { RegionSelectionMode } from "./useRegionViewer";

const props = defineProps<{
  sessionId: string; jobId: string; status: string; failure: string | null;
  stage?: string; progress?: number;
}>();
const emit = defineEmits<{ refresh: []; confirmed: []; back: [] }>();
const client = new LocalApiClient(platformBridge());
const first = shallowRef<ImageryCandidate | null>(null);
const reviewMap = shallowRef<Awaited<ReturnType<typeof loadImageryReviewMap>>>(null);
const excludedByUser = shallowRef<Uint8Array>(new Uint8Array());
const savedSelection = shallowRef<Uint8Array | null>(null);
const undoSnapshot = shallowRef<Uint8Array | null>(null);
const minimum = ref<number | string>(0);
const refine = ref(false);
const refineMode = ref<ImageryRefinement["mode"]>("greenness");
const refineStrength = ref(50);
const refineMinimum = ref("12");
const validRefineMinimum = computed(() => /^\d+$/.test(refineMinimum.value));
const refineMinimumPixels = computed<string>(previous => validRefineMinimum.value
  ? refineMinimum.value.replace(/^0+(?=\d)/, "") : previous ?? "12");
const refineHelpOpen = ref(false);
const refinementPending = ref(false);
let appliedRefinement: ImageryRefinement | undefined;
let refineTimer: ReturnType<typeof setTimeout> | undefined;
const searchOpen = ref(false);
const searchText = ref("");
const searchInput = ref<HTMLInputElement | null>(null);
const searchTrigger = ref<HTMLButtonElement | null>(null);
const searchList = ref<HTMLUListElement | null>(null);
const onlyExcluded = ref(false);
const searchPage = ref(1);
const locatedId = ref<string | null>(null);
const locatingId = ref<string | null>(null);
const overviewLayer = shallowRef<{
  canvas: HTMLCanvasElement; transform: ImageryCandidate["preview_transform"];
  pick: (x: number, y: number) => string | undefined;
  pickArea: (corners: readonly { x: number; y: number }[], covered?: (x: number, y: number) => boolean) => string[];
} | null>(null);
const tiles = shallowRef<ImageryDetailTile[]>([]);
let detail: ReturnType<typeof createImageryDetail> | undefined;
const canvas = ref<InstanceType<typeof RegionCanvas> | null>(null);
const showOverlay = ref(true);
const loading = ref(false);
const busy = ref(false);
const confirmationJobId = ref<string | null>(null);
let confirmationTimer: ReturnType<typeof setTimeout> | undefined;
const errorMessage = ref<string | null>(null);
let disposed = false;
let paintFrame: number | undefined;
let locateRequest = 0;
let lastLocatedPage: ImageryCandidate | null = null;
const format = new Intl.NumberFormat("zh-CN");
const SEARCH_PAGE_SIZE = 8;

const bounds = ([min_x, min_y, min_z, max_x, max_y, max_z]: ImageryCandidate["bounds"]): Bounds3D =>
  ({ min_x, min_y, min_z, max_x, max_y, max_z });
const imageBounds = computed(() => first.value ? bounds(first.value.bounds) : null);
const backdrop = computed(() => first.value?.preview_png_base64 ? {
  url: `data:image/png;base64,${first.value.preview_png_base64}`,
  transform: first.value.preview_transform,
} : null);
const index = computed(() => reviewMap.value?.index);
const candidateCount = computed(() => index.value?.length ?? 0);
const validMinimum = computed(() => Number.isSafeInteger(Number(minimum.value)) && Number(minimum.value) >= 0);
const minPixels = computed<number>(previous => validMinimum.value ? Number(minimum.value) : previous ?? 0);
const matchesFilter = (pixels: number | null) => pixels === null || pixels >= minPixels.value;
const ordinalOf = (id: string) => index.value?.ordinalOf(id) ?? -1;
const isEligible = (ordinal: number) => ordinal >= 0 && matchesFilter(index.value!.pixelsAt(ordinal));
const isSelected = (id: string) => !hasRegion(excludedByUser.value, ordinalOf(id));
const paintOrdinal = (ordinal: number) => !isEligible(ordinal) ? 0 : hasRegion(excludedByUser.value, ordinal) ? 2 : 1;
const paintState = (id: string) => paintOrdinal(ordinalOf(id));
const selectionSummary = computed(() => {
  const current = index.value, threshold = minPixels.value;
  const excluded = excludedByUser.value, saved = savedSelection.value;
  let eligible = 0, selected = 0, changed = !saved;
  for (let ordinal = 0; current && ordinal < current.length; ordinal++) {
    const pixels = current.pixelsAt(ordinal);
    const allowed = pixels === null || pixels >= threshold;
    const keep = allowed && !hasRegion(excluded, ordinal);
    if (allowed) eligible++;
    if (keep) selected++;
    if (!changed && keep !== hasRegion(saved!, ordinal)) changed = true;
  }
  return { eligible, selected, changed };
});
const eligibleCount = computed(() => selectionSummary.value.eligible);
const hiddenCount = computed(() => candidateCount.value - eligibleCount.value);
const selectedCount = computed(() => selectionSummary.value.selected);
const manualCount = computed(() => eligibleCount.value - selectedCount.value);
const lockedReason = computed(() => first.value?.selection_locked_reason);
const confirming = computed(() => busy.value || confirmationJobId.value !== null);
const selectionDisabled = computed(() => confirming.value || Boolean(lockedReason.value));
const refinementOptions = computed<ImageryConfirmationRefinement | null>(() => refine.value ? ({
  mode: refineMode.value, strength: refineMode.value === "patches" ? 0 : refineStrength.value,
  minimum: refineMinimumPixels.value, min_region_pixels: String(minPixels.value),
}) : null);
const unchanged = computed(() => {
  if (selectionSummary.value.changed) return false;
  const before = first.value?.confirmed_refinement, after = refinementOptions.value;
  if (!before || !after) return !before && !after;
  return before.mode === after.mode && before.strength === after.strength && before.minimum === after.minimum
    && before.min_region_pixels === after.min_region_pixels;
});
const canConfirm = computed(() => !selectionDisabled.value && !loading.value && !unchanged.value && selectedCount.value > 0
  && validMinimum.value && (!refine.value || validRefineMinimum.value));
const searchOrder = computed(() => index.value ? areaOrder(index.value) : new Uint32Array());
const searchResults = computed(() => {
  const current = index.value;
  if (!searchOpen.value || !current) return new Uint32Array();
  const query = searchText.value.trim().toLowerCase().replace(/\s+/g, "");
  const number = /^(?:区域|region_?)?0*(\d+)$/.exec(query)?.[1];
  const excluded = excludedByUser.value, excludedOnly = onlyExcluded.value, threshold = minPixels.value;
  if (current.packed && number !== undefined) {
    const ordinal = Number(number) - 1;
    return ordinal >= 0 && ordinal < current.length && isEligible(ordinal) && (!excludedOnly || hasRegion(excluded, ordinal))
      ? Uint32Array.of(ordinal) : new Uint32Array();
  }
  return searchOrder.value.filter(ordinal => {
    const pixels = current.pixelsAt(ordinal);
    if ((pixels !== null && pixels < threshold) || (excludedOnly && !hasRegion(excluded, ordinal))) return false;
    if (!query) return true;
    const item = current.itemAt(ordinal);
    if (number !== undefined) return Number(/\d+$/.exec(item.region_id)?.[0]) === Number(number);
    return item.label.toLowerCase().replace(/\s+/g, "").includes(query) || item.region_id.toLowerCase().includes(query);
  });
});
const searchPageCount = computed(() => Math.max(1, Math.ceil(searchResults.value.length / SEARCH_PAGE_SIZE)));
const searchRows = computed(() => Array.from(
  searchResults.value.subarray((searchPage.value - 1) * SEARCH_PAGE_SIZE, searchPage.value * SEARCH_PAGE_SIZE),
  ordinal => index.value!.itemAt(ordinal),
));
const toggleSearch = async () => {
  searchOpen.value = !searchOpen.value;
  await nextTick();
  if (searchOpen.value) searchInput.value?.focus();
  else searchTrigger.value?.focus();
};
const active = computed(() => ["queued", "running", "cancelling"].includes(props.status));
const phaseLabels: Record<string, string> = {
  preparing_inputs: "正在准备影像文件",
  imagery_loading_model: "正在加载识别模型",
  imagery_inferring: "正在分块识别影像",
  imagery_building_preview: "正在生成底图预览",
  imagery_vectorizing: "正在提取区域边界",
  imagery_stitching: "正在拼接跨块区域",
  imagery_merging: "正在合并相连区域",
  imagery_writing_result: "正在保存候选区域",
  persisting_result: "正在保存候选区域",
};
const reportError = (error: unknown) => { errorMessage.value = error instanceof Error ? error.message : "影像操作未完成。"; };

const getPage = (page: number) => client.imageryCandidate(props.sessionId, props.jobId, page);
const locate = async (item: ImageryReviewItem) => {
  const request = ++locateRequest;
  locatingId.value = item.region_id;
  errorMessage.value = null;
  try {
    const page = first.value?.page === item.page ? first.value
      : lastLocatedPage?.page === item.page ? lastLocatedPage : await getPage(item.page);
    if (disposed || request !== locateRequest || !isEligible(ordinalOf(item.region_id))) return;
    lastLocatedPage = page;
    const region = page.regions.find(row => row.region_id === item.region_id);
    if (!region) throw new Error("无法定位此区域。");
    const grid = first.value?.source_grid?.transform ?? first.value?.preview_transform;
    const pixelSize = grid ? Math.max(Math.hypot(grid[0], grid[3]), Math.hypot(grid[1], grid[4])) : 1;
    locatedId.value = item.region_id;
    showOverlay.value = true;
    canvas.value?.focusRegion(bounds(region.bbox), pixelSize * 100);
  } catch (error) {
    if (!disposed && request === locateRequest) reportError(error);
  } finally {
    if (request === locateRequest) locatingId.value = null;
  }
};
const load = async () => {
  if (loading.value || props.status !== "succeeded" || reviewMap.value) return;
  loading.value = true;
  errorMessage.value = null;
  try {
    const result = await getPage(1);
    if (disposed) return;
    const map = await loadImageryReviewMap(result, getPage, () => disposed,
      start => client.imageryReviewIndex(props.sessionId, props.jobId, start));
    if (!map || disposed) return;
    if (result.confirmed_region_bits || result.confirmed_region_ids) {
      const length = map.index.length;
      const saved = result.confirmed_region_bits ? decodeRegionBits(result.confirmed_region_bits, length) : new Uint8Array(Math.ceil(length / 8));
      for (const id of result.confirmed_region_ids ?? []) {
        const ordinal = map.index.ordinalOf(id);
        if (ordinal < 0) throw new Error("已确认范围与候选索引不一致。");
        markRegion(saved, ordinal);
      }
      savedSelection.value = saved;
      excludedByUser.value = saved.map(byte => 255 ^ byte);
    }
    first.value = result;
    reviewMap.value = map;
    if (result.confirmed_refinement && result.refinement_available) {
      const options = result.confirmed_refinement;
      refineMode.value = options.mode;
      if (options.mode === "greenness") refineStrength.value = options.strength;
      refineMinimum.value = options.minimum;
      minimum.value = options.min_region_pixels;
      refine.value = true;
    }
    if (result.source_grid) {
      detail = createImageryDetail(result.source_grid, result.preview_transform,
        (level, column, row) => client.imageryTile(props.sessionId, props.jobId, level, column, row, appliedRefinement),
        paintState, value => { tiles.value = value; }, pending => { refinementPending.value = pending; });
    }
    if (result.pending_confirmation_job_id) observeConfirmation(result.pending_confirmation_job_id);
  } catch (error) {
    if (!disposed) reportError(error);
  } finally {
    loading.value = false;
  }
};

const toggle = (ids: string[], mode: RegionSelectionMode = "toggle") => {
  if (selectionDisabled.value) return;
  const changed = ids.map(ordinalOf).filter(ordinal => isEligible(ordinal)
    && (mode !== "add" || hasRegion(excludedByUser.value, ordinal)));
  if (!changed.length) return;
  const next = new Uint8Array(Math.ceil(candidateCount.value / 8));
  next.set(excludedByUser.value);
  for (const ordinal of changed) next[ordinal >> 3] ^= 1 << (ordinal & 7);
  undoSnapshot.value = excludedByUser.value;
  excludedByUser.value = next;
};

const undo = () => {
  if (!undoSnapshot.value || selectionDisabled.value) return;
  excludedByUser.value = undoSnapshot.value;
  undoSnapshot.value = null;
};

const confirm = async () => {
  if (!canConfirm.value) return;
  busy.value = true;
  errorMessage.value = null;
  const base = first.value?.confirmed_region_set_id;
  try {
    const current = index.value!;
    const selection = current.packed ? packedSelection(current.length) : legacySelection(current);
    const refinement = refinementOptions.value;
    const job = await client.submitImageryConfirmation(props.sessionId, props.jobId,
      { ...selection, ...(base ? { expected_region_set_id: base } : {}), ...(refinement ? { refinement } : {}) });
    if (!disposed) observeConfirmation(job.job_id);
  } catch (error) {
    if (!disposed && (!(error instanceof LocalApiError) || error.status === null || error.status === 409)) {
      try {
        const current = await getPage(1);
        if (disposed) return;
        if (current.pending_confirmation_job_id) {
          observeConfirmation(current.pending_confirmation_job_id);
          return;
        }
        if (current.confirmed_region_set_id && current.confirmed_region_set_id !== base) {
          emit("confirmed");
          return;
        }
      } catch { /* Keep the original submission error. */ }
    }
    if (!disposed) reportError(error);
  } finally {
    busy.value = false;
  }
};

function observeConfirmation(jobId: string) {
  confirmationJobId.value = jobId;
  const poll = async () => {
    if (disposed || confirmationJobId.value !== jobId) return;
    let nextDelay = 300;
    try {
      const state = await client.projectJob(props.sessionId, jobId);
      if (disposed || confirmationJobId.value !== jobId) return;
      if (["succeeded", "failed", "cancelled", "interrupted"].includes(state.status)) {
        confirmationJobId.value = null;
        if (state.status === "succeeded") emit("confirmed");
        else if (state.status !== "cancelled") errorMessage.value = state.error?.message ?? "确认未完成，原始候选已保留。";
        return;
      }
    } catch (error) {
      if (disposed || confirmationJobId.value !== jobId) return;
      if (error instanceof LocalApiError && error.status === 404) {
        confirmationJobId.value = null;
        reportError(error);
        return;
      }
      nextDelay = 1000;
    }
    confirmationTimer = setTimeout(() => void poll(), nextDelay);
  };
  void poll();
}

const packedSelection = (count: number) => {
  const bits = new Uint8Array(Math.ceil(count / 8));
  const current = index.value!, threshold = minPixels.value, excluded = excludedByUser.value;
  for (let ordinal = 0; ordinal < count; ordinal++) {
    const pixels = current.pixelsAt(ordinal);
    if ((pixels === null || pixels >= threshold) && !hasRegion(excluded, ordinal)) markRegion(bits, ordinal);
  }
  return { included_region_bits: encodeRegionBits(bits) };
};

const legacySelection = (current: NonNullable<typeof index.value>) => {
  const useExcluded = current.length - selectedCount.value < selectedCount.value;
  const ids: string[] = [];
  for (let ordinal = 0; ordinal < current.length; ordinal++) {
    if ((paintOrdinal(ordinal) === 1) !== useExcluded) ids.push(current.at(ordinal));
  }
  return useExcluded ? { excluded_region_ids: ids } : { included_region_ids: ids };
};

const cancel = async () => {
  if (busy.value || !active.value) return;
  busy.value = true;
  errorMessage.value = null;
  try {
    await client.cancelJob(props.sessionId, props.jobId);
    if (!disposed) emit("refresh");
  } catch (error) {
    if (!disposed) reportError(error);
  } finally {
    busy.value = false;
  }
};

const cancelConfirmation = async () => {
  if (!confirmationJobId.value || busy.value) return;
  busy.value = true;
  try {
    await client.cancelJob(props.sessionId, confirmationJobId.value);
  } catch (error) {
    if (!disposed) reportError(error);
  } finally {
    busy.value = false;
  }
};

watch(() => props.status, () => void load(), { immediate: true });
watch([refine, refineMode, refineStrength, refineMinimumPixels], () => {
  if (refineTimer !== undefined) clearTimeout(refineTimer);
  const apply = () => {
    refineTimer = undefined;
    appliedRefinement = refine.value ? {
      mode: refineMode.value, strength: refineStrength.value, minimum: refineMinimumPixels.value,
    } : undefined;
    detail?.refresh();
  };
  if (!refine.value || !appliedRefinement || appliedRefinement.mode !== refineMode.value) apply();
  else refineTimer = setTimeout(apply, 180);
  if (refine.value) showOverlay.value = true;
});
watch(() => props.failure, (failure) => { if (failure) errorMessage.value = failure; }, { immediate: true });
watch([minPixels, searchText, onlyExcluded], () => { searchPage.value = 1; });
watch(searchPageCount, count => { searchPage.value = Math.min(searchPage.value, count); });
watch([searchPage, searchText, onlyExcluded], () => {
  if (searchList.value) searchList.value.scrollTop = 0;
}, { flush: "post" });
watch(minPixels, () => {
  if (locatedId.value && !isEligible(ordinalOf(locatedId.value))) {
    locatedId.value = null;
    canvas.value?.clearFocus();
  }
});
watch([reviewMap, minPixels, excludedByUser], () => {
  if (paintFrame !== undefined) return;
  paintFrame = requestAnimationFrame(() => {
    paintFrame = undefined;
    const map = reviewMap.value;
    const transform = first.value?.preview_transform;
    if (!map || !transform) return;
    const threshold = minPixels.value, excluded = excludedByUser.value;
    map.paint(paintState, ordinal => {
      const pixels = map.index.pixelsAt(ordinal);
      return pixels !== null && pixels < threshold ? 0 : hasRegion(excluded, ordinal) ? 2 : 1;
    });
    detail?.repaint();
    overviewLayer.value = { canvas: map.canvas, transform, pick: map.pick, pickArea: map.pickArea };
  });
});
onBeforeUnmount(() => {
  disposed = true;
  detail?.close();
  if (refineTimer !== undefined) clearTimeout(refineTimer);
  if (confirmationTimer !== undefined) clearTimeout(confirmationTimer);
  if (paintFrame !== undefined) cancelAnimationFrame(paintFrame);
});
</script>

<template>
  <section v-if="first" class="region-stage imagery-review">
    <div class="region-viewer-area">
      <RegionCanvas ref="canvas" :bounds="imageBounds" :regions="[]"
        :selected-region-ids="[]" :committed-region-ids="[]" :loading="false" :backdrop="backdrop"
        :overview="showOverlay ? overviewLayer : null" :imagery-tiles="tiles"
        @view-changing="detail?.suspend()"
        @viewport="(bounds, pixelsPerMeter) => detail?.update(bounds, pixelsPerMeter)"
        imagery-review @selection="toggle($event.ids, $event.mode)" />
      <div class="region-viewer-actions imagery-review__tools">
        <button type="button" title="原图" aria-label="原图" :aria-pressed="!showOverlay" @click="showOverlay = false"><Image :size="17" /></button>
        <button type="button" title="显示区域" aria-label="显示区域" :aria-pressed="showOverlay" @click="showOverlay = true"><Layers :size="17" /></button>
        <button type="button" title="适配视图" aria-label="适配视图" @click="locatedId = null; canvas?.resetRotation()"><Maximize2 :size="17" /></button>
      </div>
    </div>
    <aside class="region-inspector imagery-review__inspector" aria-label="种植范围验收">
      <header class="imagery-review__heading">
        <button v-if="searchOpen" class="icon-button imagery-review__back" type="button" title="返回验收" aria-label="返回验收" @click="toggleSearch"><ArrowLeft :size="17" /></button>
        <ScanLine v-else :size="19" class="imagery-review__heading-icon" />
        <h2>{{ searchOpen ? '查找区域' : '确认种植范围' }}</h2>
      </header>
      <DismissibleNotice class="region-error" :message="errorMessage" @dismiss="errorMessage = null" />
      <div v-if="!searchOpen" class="imagery-review__content">
        <section class="imagery-review__summary" aria-label="区域摘要">
          <div class="imagery-review__total" aria-live="polite">
            <strong data-testid="imagery-retained">{{ format.format(selectedCount) }}</strong><span>{{ refine ? "个候选区域已选" : "个区域将保留" }}</span>
          </div>
          <dl class="imagery-review__counts">
            <div><dt>识别候选</dt><dd>{{ format.format(candidateCount) }}</dd></div>
            <div v-if="manualCount"><dt>{{ first.confirmed_region_set_id ? '未纳入' : '手动排除' }}</dt><dd>{{ format.format(manualCount) }}</dd></div>
          </dl>
        </section>
        <section class="imagery-review__filter">
          <div class="imagery-review__line">
            <label for="imagery-min-region-pixels"><Filter :size="15" /> 最小区域</label>
            <button type="button" class="imagery-review__reset" :disabled="selectionDisabled || minimum === 0" @click="minimum = 0">重置</button>
          </div>
          <div class="imagery-review__number" :class="{ 'is-invalid': !validMinimum }">
            <input id="imagery-min-region-pixels" v-model.number="minimum" type="number" min="0" step="1"
              aria-label="最小区域像素数" :disabled="selectionDisabled" :aria-invalid="!validMinimum" aria-describedby="imagery-filter-count" />
            <span aria-hidden="true">像素</span>
          </div>
          <small id="imagery-filter-count" :class="{ 'is-invalid': !validMinimum }">
            {{ !validMinimum ? "请输入非负整数" : !eligibleCount ? "没有符合阈值的区域" : hiddenCount ? `已隐藏 ${format.format(hiddenCount)} 个小区域` : "未隐藏小区域" }}
          </small>
        </section>
        <section v-if="first.source_grid && first.refinement_available" class="imagery-review__filter imagery-review__refine">
          <div class="imagery-review__line">
            <div class="imagery-review__refine-title" @pointerenter="refineHelpOpen = true" @pointerleave="refineHelpOpen = false">
              <label for="imagery-refine-toggle"><SlidersHorizontal :size="15" /> 细化种植范围</label>
              <button type="button" class="imagery-review__info" aria-label="细化种植范围说明"
                      aria-describedby="imagery-refine-help" @focus="refineHelpOpen = true" @blur="refineHelpOpen = false"
                      @click="refineHelpOpen = true" @keydown.esc.stop="refineHelpOpen = false">
                <Info :size="15" />
              </button>
              <span v-show="refineHelpOpen" id="imagery-refine-help" role="tooltip" class="imagery-review__tooltip">
                在已识别的植被区域内，尽可能标出裸岩、裸土等建议留白的位置。
              </span>
            </div>
            <input id="imagery-refine-toggle" v-model="refine" type="checkbox" role="switch"
              :disabled="confirming" />
          </div>
          <template v-if="refine">
            <div class="imagery-review__segments" role="group" aria-label="细化方式">
              <button type="button" :disabled="confirming" :aria-pressed="refineMode === 'patches'" @click="refineMode = 'patches'">自动留白</button>
              <button type="button" :disabled="confirming" :aria-pressed="refineMode === 'greenness'" @click="refineMode = 'greenness'">手动调节</button>
            </div>
            <small v-if="refineMode === 'patches'">结合颜色和周边像素，自动标出建议留白的斑块。</small>
            <div v-if="refineMode === 'greenness'" class="imagery-review__strength">
              <label for="imagery-refine-strength">留白强度 <output>{{ refineStrength }}</output></label>
              <input id="imagery-refine-strength" v-model.number="refineStrength" type="range" min="0" max="100" :disabled="confirming" />
              <small><span>少留白</span><span>多留白</span></small>
            </div>
            <label for="imagery-refine-minimum">最小留白斑块</label>
            <div class="imagery-review__number" :class="{ 'is-invalid': !validRefineMinimum }">
              <input id="imagery-refine-minimum" v-model="refineMinimum" inputmode="numeric"
                     :disabled="confirming" :aria-invalid="!validRefineMinimum" aria-describedby="imagery-refine-minimum-hint" />
              <span>像素</span>
            </div>
            <small id="imagery-refine-minimum-hint" :class="{ 'is-invalid': !validRefineMinimum }">
              {{ validRefineMinimum ? "0 表示不限" : "请输入非负整数" }}
            </small>
            <small v-if="refinementPending" role="status">正在统计留白斑块…</small>
            <small v-else-if="tiles.some(tile => tile.refinementUnavailable)" role="status">无法计算细化预览，原有种植范围不变。</small>
            <small v-else-if="!tiles.some(tile => tile.refined)" role="status">近景细化 · 仅预览</small>
          </template>
        </section>
        <div class="imagery-review__actions">
          <button v-if="!lockedReason" class="imagery-review__action" type="button" :disabled="confirming || !undoSnapshot" @click="undo"><Undo2 :size="16" /> 撤销上次调整</button>
          <button ref="searchTrigger" class="imagery-review__action" type="button" @click="toggleSearch">
            <Search :size="16" /> 查找区域 <ChevronRight :size="15" class="imagery-review__chevron" />
          </button>
        </div>
      </div>
      <section v-else class="imagery-review__search" aria-label="查找区域">
        <div class="imagery-review__query">
          <Search :size="15" />
          <input ref="searchInput" v-model="searchText" type="search" aria-label="搜索区域编号" placeholder="搜索区域编号" />
        </div>
        <div class="imagery-review__segments" role="group" aria-label="搜索范围">
          <button type="button" :aria-pressed="!onlyExcluded" @click="onlyExcluded = false">全部</button>
          <button type="button" :aria-pressed="onlyExcluded" @click="onlyExcluded = true">已排除</button>
        </div>
        <div class="imagery-review__result-head"><span>{{ format.format(searchResults.length) }} 个结果</span><span>面积从大到小</span></div>
        <p v-if="!searchResults.length" class="imagery-review__empty">{{ onlyExcluded && !searchText ? '没有手动排除的区域' : '没有匹配的区域' }}</p>
        <ul v-else ref="searchList" class="imagery-review__results" aria-label="区域查找结果">
          <li v-for="region in searchRows" :key="region.region_id" :class="{ 'is-located': locatedId === region.region_id }">
            <button type="button" class="imagery-review__locate" :title="`定位 ${region.label}`"
              :aria-label="`定位 ${region.label}`" :disabled="confirming || locatingId === region.region_id" @click="locate(region)">
              <LoaderCircle v-if="locatingId === region.region_id" :size="15" class="spinning" /><Crosshair v-else :size="15" />
              <span><strong>{{ region.label }}</strong><small v-if="region.pixel_count !== null">{{ format.format(region.pixel_count) }} 像素</small></span>
            </button>
            <input type="checkbox" :aria-label="`保留 ${region.label}`" :checked="isSelected(region.region_id)"
              :disabled="selectionDisabled" @change="toggle([region.region_id])" />
          </li>
        </ul>
        <nav v-if="searchPageCount > 1" class="imagery-review__pages" aria-label="查找结果分页">
          <button class="icon-button" type="button" title="上一页" aria-label="上一页" :disabled="searchPage <= 1" @click="searchPage--"><ChevronLeft :size="16" /></button>
          <span>{{ searchPage }} / {{ searchPageCount }}</span>
          <button class="icon-button" type="button" title="下一页" aria-label="下一页" :disabled="searchPage >= searchPageCount" @click="searchPage++"><ChevronRight :size="16" /></button>
        </nav>
      </section>
      <footer>
        <div v-if="showOverlay" class="imagery-review__legend" aria-label="地图图例">
          <span><i class="is-kept" /> 保留</span><span><i class="is-excluded" /> {{ first.confirmed_region_set_id ? '未纳入' : '手动排除' }}</span>
          <span v-if="refine"><i class="is-proposed" /> 拟留白</span>
        </div>
        <template v-if="lockedReason">
          <p class="imagery-review__locked"><LockKeyhole :size="14" /> {{ lockedReason }}</p>
          <button class="secondary-button" type="button" @click="emit('back')">返回区域选择 <ChevronRight :size="16" /></button>
        </template>
        <template v-else>
          <button class="primary-button" type="button" :disabled="!canConfirm" @click="confirm">
            <LoaderCircle v-if="confirming" :size="16" class="spinning" /><Check v-else :size="16" />
            {{ confirming ? '正在确认' : '确认提取区域' }}
          </button>
          <button v-if="confirmationJobId" class="secondary-button" type="button" :disabled="busy" @click="cancelConfirmation">
            <X :size="16" /> 取消确认
          </button>
        </template>
      </footer>
    </aside>
  </section>
  <section v-else class="stage-overview imagery-review__status">
    <div class="stage-overview__icon"><LoaderCircle v-if="active || loading" :size="24" class="spinning" /><Image v-else :size="24" /></div>
    <h2>{{ status === "cancelling" ? "正在取消影像识别" : active ? (phaseLabels[stage ?? ""] ?? "等待识别影像") : loading ? "正在加载全图候选" : status === "cancelled" ? "影像识别已取消" : "影像识别结果不可用" }}</h2>
    <progress v-if="status === 'running'" class="imagery-review__progress" max="1" :value="progress ?? 0" aria-label="影像识别进度" />
    <DismissibleNotice :message="errorMessage" @dismiss="errorMessage = null" />
    <button v-if="active" class="secondary-button" type="button" :disabled="busy || status === 'cancelling'" @click="cancel"><X :size="16" /> 取消识别</button>
    <button v-else-if="status === 'succeeded' && !loading" class="secondary-button" type="button" @click="load()"><RotateCcw :size="16" /> 重新读取</button>
  </section>
</template>

<style scoped>
.imagery-review { grid-template-columns: minmax(0, 1fr) clamp(212px, 28%, 284px); }
.imagery-review__tools button[aria-pressed="true"] { color: #0b7268; background: #d8f0ea; }
.imagery-review__inspector { overflow: hidden; }
.imagery-review__inspector > .imagery-review__heading { display: flex; align-items: center; gap: 10px; padding: 18px; }
.imagery-review__heading h2 { margin: 0; font-size: 15px; }
.imagery-review__heading-icon { box-sizing: content-box; flex: none; padding: 6px; border-radius: 6px; color: var(--color-accent-hover); background: var(--color-accent-soft); }
.imagery-review__back { width: 31px; min-width: 31px; height: 31px; }
.imagery-review__content { flex: 1; min-height: 0; overflow: auto; }
.imagery-review__summary { padding: 18px; background: var(--color-surface-soft); border-bottom: 1px solid var(--color-border); }
.imagery-review__total { display: flex; align-items: baseline; flex-wrap: wrap; gap: 4px 9px; }
.imagery-review__total strong { color: var(--color-accent-hover); font-size: 33px; line-height: 1.12; font-variant-numeric: tabular-nums; }
.imagery-review__total span { color: var(--color-text); font-size: 12px; font-weight: 620; }
.imagery-review__counts { display: grid; gap: 6px; margin: 11px 0 0; }
.imagery-review__counts > div, .imagery-review__line { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.imagery-review__counts dt { color: var(--color-text-muted); font-size: 12px; }
.imagery-review__counts dd { margin: 0; color: var(--color-text); font-size: 12px; font-weight: 620; font-variant-numeric: tabular-nums; }
.imagery-review__filter { display: grid; gap: 8px; padding: 15px 18px; border-bottom: 1px solid var(--color-border); }
.imagery-review__filter label { display: flex; align-items: center; gap: 7px; font-size: 13px; font-weight: 650; }
.imagery-review__filter label svg { color: var(--color-text-muted); }
.imagery-review__reset { padding: 2px 0 2px 6px; border: 0; background: transparent; color: var(--color-accent-hover); font-size: 12px; }
.imagery-review__reset:disabled { color: var(--color-text-faint); }
.imagery-review__number, .imagery-review__query { display: flex; align-items: center; min-height: 34px; border: 1px solid var(--color-border-strong); border-radius: 6px; }
.imagery-review__number:focus-within, .imagery-review__query:focus-within { border-color: var(--color-accent); outline: 2px solid var(--color-accent-soft); }
.imagery-review__number.is-invalid { border-color: #b45140; }
.imagery-review__number input, .imagery-review__query input { flex: 1; width: 100%; min-width: 0; height: 34px; padding: 0 9px; border: 0; outline: 0; background: transparent; color: var(--color-text); font: inherit; font-size: 13px; }
.imagery-review__number input { font-variant-numeric: tabular-nums; }
.imagery-review__number input::-webkit-inner-spin-button { appearance: none; }
.imagery-review__number > span { padding: 0 10px 0 7px; color: var(--color-text-muted); font-size: 12px; white-space: nowrap; }
.imagery-review__filter small, .imagery-review__empty { color: var(--color-text-muted); font-size: 12px; }
.imagery-review__filter small.is-invalid { color: #a83f32; }
.imagery-review__refine { position: relative; }
.imagery-review__refine .imagery-review__segments { margin-top: 0; }
.imagery-review__refine-title { display: flex; align-items: center; gap: 3px; }
.imagery-review__info { display: grid; width: 24px; height: 24px; flex: none; place-items: center; padding: 0; border: 0; border-radius: 4px; background: transparent; color: var(--color-text-muted); cursor: help; }
.imagery-review__info:hover, .imagery-review__info:focus-visible { background: var(--color-accent-soft); color: var(--color-accent-hover); }
.imagery-review__tooltip { position: absolute; z-index: 2; inset-inline: 18px; top: 39px; padding: 8px 10px; border: 1px solid var(--color-border-strong); border-radius: 6px; background: var(--color-surface); box-shadow: 0 4px 12px #18352a1f; color: var(--color-text); font-size: 12px; line-height: 1.5; pointer-events: none; }
.imagery-review__refine input { accent-color: var(--color-accent); }
.imagery-review__refine input[role="switch"] { appearance: none; flex: none; display: grid; align-items: center; width: 32px; height: 18px; padding: 2px; margin: 0; border: 1px solid var(--color-border-strong); border-radius: 12px; background: var(--color-surface-soft); cursor: pointer; }
.imagery-review__refine input[role="switch"]::before { content: ""; width: 12px; height: 12px; border-radius: 50%; background: var(--color-text-faint); }
.imagery-review__refine input[role="switch"]:checked { border-color: var(--color-accent); background: var(--color-accent); }
.imagery-review__refine input[role="switch"]:checked::before { justify-self: end; background: white; }
.imagery-review__refine input[role="switch"]:disabled { opacity: .55; cursor: not-allowed; }
.imagery-review__strength { display: grid; gap: 4px; margin: 4px 0; }
.imagery-review__strength label, .imagery-review__strength small { display: flex; justify-content: space-between; }
.imagery-review__strength input { width: 100%; margin: 0; }
.imagery-review__strength output { font-variant-numeric: tabular-nums; color: var(--color-accent-hover); }
.imagery-review__actions { display: grid; gap: 3px; padding: 9px 10px; }
.imagery-review__action { display: flex; align-items: center; width: 100%; min-height: 36px; gap: 9px; padding: 6px 8px; border: 0; border-radius: 5px; background: transparent; color: var(--color-text); font: inherit; font-size: 13px; text-align: start; }
.imagery-review__action svg { flex: none; color: var(--color-text-muted); }
.imagery-review__action:hover:not(:disabled) { background: var(--color-surface-soft); color: var(--color-accent-hover); }
.imagery-review__action:disabled, .imagery-review__action:disabled svg { color: var(--color-text-faint); }
.imagery-review__chevron { margin-inline-start: auto; }
.imagery-review__search { display: flex; flex-direction: column; flex: 1; min-height: 0; padding: 14px 18px 12px; }
.imagery-review__search > :not(.imagery-review__results) { flex-shrink: 0; }
.imagery-review__query > svg { flex: none; margin-left: 8px; color: var(--color-text-muted); }
.imagery-review__segments { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 3px; padding: 3px; margin-top: 8px; border: 1px solid var(--color-border); border-radius: 6px; background: var(--color-surface-soft); }
.imagery-review__segments button { min-height: 27px; padding: 3px; border: 1px solid transparent; border-radius: 4px; background: transparent; color: var(--color-text-muted); font-size: 12px; }
.imagery-review__segments button[aria-pressed="true"] { border-color: var(--color-border); background: var(--color-surface); color: var(--color-accent-hover); font-weight: 650; }
.imagery-review__result-head { display: flex; align-items: center; justify-content: space-between; gap: 5px; margin-top: 12px; padding-bottom: 6px; border-bottom: 1px solid var(--color-border); color: var(--color-text-muted); font-size: 11px; }
.imagery-review__empty { margin: 12px 0; }
.imagery-review__results { display: grid; min-height: 0; align-content: start; margin: 0; padding: 0; overflow: auto; list-style: none; }
.imagery-review__results li { display: flex; align-items: center; gap: 8px; padding: 0 4px; border-bottom: 1px solid var(--color-border); }
.imagery-review__results li.is-located { background: var(--color-accent-soft); }
.imagery-review__locate { display: flex; align-items: center; gap: 8px; flex: 1; min-width: 0; min-height: 40px; padding: 5px 0; border: 0; background: transparent; color: var(--color-text); text-align: start; }
.imagery-review__locate > svg { flex: none; color: var(--color-text-muted); }
.imagery-review__locate:hover > svg { color: var(--color-accent-hover); }
.imagery-review__locate > span { display: grid; gap: 2px; min-width: 0; }
.imagery-review__locate strong, .imagery-review__locate small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.imagery-review__locate strong { font-size: 12px; font-weight: 630; }
.imagery-review__locate small { color: var(--color-text-muted); font-size: 11px; font-variant-numeric: tabular-nums; }
.imagery-review__results input { flex: none; margin: 0; accent-color: var(--color-accent); }
.imagery-review__pages { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding-top: 8px; color: var(--color-text-muted); font-size: 12px; }
.imagery-review__inspector > footer { grid-template-columns: minmax(0, 1fr); }
.imagery-review__inspector > footer button { min-width: 0; }
.imagery-review__locked { display: flex; align-items: flex-start; gap: 7px; margin: 0; color: var(--color-text-muted); font-size: 12px; }
.imagery-review__locked svg { flex: none; }
.imagery-review__legend { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 13px; min-height: 16px; color: var(--color-text-muted); font-size: 11px; }
.imagery-review__legend span { display: inline-flex; align-items: center; gap: 5px; }
.imagery-review__legend i { width: 10px; height: 8px; border-radius: 2px; }
.imagery-review__legend i.is-kept { border: 1px solid #39a783; background: #bce5d6; }
.imagery-review__legend i.is-excluded { border: 1px solid #b67b37; }
.imagery-review__legend i.is-proposed { border: 1px solid #c58d3c; background: #ffc67b; }
.imagery-review__progress { width: min(240px, 100%); height: 10px; accent-color: #148e84; }
.imagery-review__status > :deep(.dismissible-notice) { max-width: 440px; }
@media (max-width: 900px) {
  .imagery-review { grid-template-columns: minmax(0, 1fr); grid-template-rows: minmax(280px, 1fr) 420px; overflow: auto; }
  .imagery-review :deep(.region-canvas-shell) { min-height: 280px; }
  .imagery-review__inspector { max-height: none; border-left: 0; border-top: 1px solid var(--color-border); }
}
</style>
