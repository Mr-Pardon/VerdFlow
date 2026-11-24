import { ref, computed, nextTick, watch, onBeforeUnmount } from "vue";
import { type LiveRegion, type LiveGeometry } from "../types";
import { exportRegionsShapefile, importRegionsShapefile, regionGeometryPage } from "../api";
import type { Ref } from "vue";
import type { DtsSdkBridge } from "../sdk/DtsSdkBridge";
import { readableError } from "../shared/presentation";
import type { SessionSnapshot } from "../types";
import { assertRegionBudget, regionPayload, geometryId, geometryKind, selectionId } from "../shared/regionLimits";
import { useActorPicking } from "./useActorPicking";
import { useRegionBrush } from "./useRegionBrush";

export function useRegionDrafts(context: {
  samplingFailures: Ref<NonNullable<SessionSnapshot["sampling_failures"]>>;
  panelCollapsed: Ref<boolean>;
  operationActive: Ref<boolean>;
  submitting: Ref<boolean>;
  errorMessage: Ref<string | null>;
  sdk: DtsSdkBridge;
  sdkReady: Ref<boolean>;
  inputGeometry?: Ref<"polygon" | "polyline">;
  brushUnavailable?: Ref<boolean>;
}) {
  const { samplingFailures, panelCollapsed, operationActive, submitting, errorMessage, sdk, sdkReady } = context;
  let stopped = false;
  const drawing = ref(false);

  const exportingRegions = ref(false);

  const importingRegions = ref(false);

  const clearingRegions = ref(false);

  const updatingRegionVisibility = ref(false);

  const clearRegionsDialog = ref<HTMLDialogElement | null>(null);

  const regionExportNotice = ref("");

  const regionExportPath = ref<string | null>(null);

  const regions = ref<(LiveGeometry & { enabled: boolean; visible: boolean })[]>([]);
  const inputGeometry = computed(() => context.inputGeometry?.value ?? "polygon");
  const pathMode = computed(() => inputGeometry.value === "polyline");
  const activeRegions = computed(() => regions.value.filter(region => geometryKind(region) === inputGeometry.value));
  const regionsById = computed(() => new Map(activeRegions.value.map(region => [geometryId(region), region])));
  const setRegionEnabled = (region: typeof regions.value[number], enabled: boolean) => {
    if (region.enabled === enabled) return;
    region.enabled = enabled;
    sdk.setRegionSelectionAppearance(geometryId(region), enabled);
  };

  const regionSearch = ref("");

  const regionPage = ref(0);

  const focusedRegionId = ref("");

  const REGION_PAGE_SIZE = 50;

  const filteredRegions = computed(() => {
    const query = regionSearch.value.trim().toLocaleLowerCase();
    return activeRegions.value.filter((region, i) => !query || region.name.toLocaleLowerCase().includes(query) || String(i + 1) === query);
  });

  const regionPageCount = computed(() => Math.max(1, Math.ceil(filteredRegions.value.length / REGION_PAGE_SIZE)));

  const visibleRegions = computed(() => filteredRegions.value.slice(regionPage.value * REGION_PAGE_SIZE, (regionPage.value + 1) * REGION_PAGE_SIZE));

  const failureByRegion = computed(() => new Map(samplingFailures.value.map(item => [selectionId(item), item.reason])));

  const revealRegion = async (regionId: string) => {
    const index = activeRegions.value.findIndex(region => geometryId(region) === regionId);
    if (index < 0) return;
    panelCollapsed.value = false;
    regionSearch.value = "";
    focusedRegionId.value = regionId;
    await nextTick();
    regionPage.value = Math.floor(index / REGION_PAGE_SIZE);
    await nextTick();
    document.getElementById(`region-row-${regionId}`)?.scrollIntoView({ block: "nearest", inline: "nearest" });
  };

  const selectFailedRegions = () => {
    if (selectionLocked.value) return;
    for (const region of activeRegions.value) setRegionEnabled(region, failureByRegion.value.has(geometryId(region)));
    const first = samplingFailures.value[0];
    if (first) void revealRegion(selectionId(first));
  };

  const appendRegisteredRegions = async (dataset: string, total: number, options: {
    signal?: AbortSignal;
    onPage?: (added: number) => void;
  } = {}) => {
    const staged: (LiveRegion & { enabled: boolean; visible: boolean })[] = [];
    let previewFailed = false;
    const assertActive = () => {
      if (stopped || options.signal?.aborted) throw new Error("已取消载入选区。");
    };
    if (!Number.isSafeInteger(total) || total <= 0) throw new Error("选区数量无效，未追加选区。");
    try {
      while (staged.length < total) {
        assertActive();
        const start = staged.length;
        const { regions: page } = await regionGeometryPage(dataset, start);
        assertActive();
        if (!page?.length || start + page.length > total ||
            page.some((region, i) => region.dataset_id !== dataset || region.ordinal !== start + i)) {
          throw new Error("选区草稿数据不完整，未追加选区。");
        }
        let visible = false;
        try { await sdk.addImportedPage(page); visible = true; }
        catch { previewFailed = true; }
        for (const region of page) {
          staged.push({ ...region, coordinates: [], holes: undefined, enabled: true, visible });
        }
        assertActive();
        options.onPage?.(staged.length);
      }
      assertActive();
      regions.value = regions.value.concat(staged);
    } catch (failure) {
      if (!stopped) {
        for (const region of staged) {
          if (!region.visible) continue;
          try { sdk.removeRegion(geometryId(region)); }
          catch { /* Preserve the import error if Explorer is closing. */ }
        }
      }
      throw failure;
    }
    if (staged[0]) void revealRegion(geometryId(staged[0]));
    return previewFailed;
  };

  const busy = computed(() => operationActive.value || submitting.value || exportingRegions.value || importingRegions.value || clearingRegions.value || updatingRegionVisibility.value);
  const actorPicking = useActorPicking(sdk, computed(() => !sdkReady.value || pathMode.value || busy.value || drawing.value),
    errorMessage, async (dataset, total, signal) => {
      const previewFailed = await appendRegisteredRegions(dataset, total, { signal });
      if (previewFailed) errorMessage.value = "选区已加入，部分贴地预览未能显示，可点击选区的显示按钮重试。";
    });
  const enabledRegionCount = computed(() => activeRegions.value.filter(region => region.enabled).length);
  const hasVisibleRegions = computed(() => activeRegions.value.some(region => region.visible));
  const regionBrush = useRegionBrush(sdk, computed(() => !sdkReady.value || busy.value || drawing.value || pathMode.value ||
    !hasVisibleRegions.value || panelCollapsed.value || actorPicking.phase.value !== "idle" || Boolean(context.brushUnavailable?.value)),
    errorMessage, (id, enabled) => {
      const region = regionsById.value.get(id);
      if (region?.visible) setRegionEnabled(region, enabled);
    });
  const selectionLocked = computed(() => busy.value || actorPicking.phase.value !== "idle" || regionBrush.dragging.value);

  const selectAllRegions = (invert = false) => {
    if (selectionLocked.value || drawing.value) return;
    for (const region of activeRegions.value) setRegionEnabled(region, invert ? !region.enabled : true);
  };

  const requestClearRegions = () => {
    if (selectionLocked.value || drawing.value || !regions.value.length) return;
    clearRegionsDialog.value?.showModal();
  };

  const clearRegions = async () => {
    if (selectionLocked.value || drawing.value) return;
    clearRegionsDialog.value?.close();
    clearingRegions.value = true;
    errorMessage.value = null;
    regionExportNotice.value = "";
    regionExportPath.value = null;
    const remaining: typeof regions.value = [];
    try {
      for (let i = 0; i < activeRegions.value.length; i++) {
        const region = activeRegions.value[i]!;
        try { sdk.removeRegion(geometryId(region)); }
        catch { remaining.push(region); }
        if (i % 20 === 19) await new Promise(resolve => window.setTimeout(resolve, 0));
      }
      regions.value = [...regions.value.filter(region => geometryKind(region) !== inputGeometry.value), ...remaining];
      regionSearch.value = "";
      regionPage.value = 0;
      focusedRegionId.value = "";
      if (remaining.length) errorMessage.value = `${remaining.length} 个选区预览未能清除，已保留在列表中，请重试`;
    } finally {
      clearingRegions.value = false;
    }
  };

  const exportRegions = async () => {
    if (pathMode.value) return;
    if (selectionLocked.value || drawing.value || !enabledRegionCount.value) return;
    exportingRegions.value = true;
    regionExportNotice.value = "";
    regionExportPath.value = null;
    errorMessage.value = null;
    try {
      const result = await exportRegionsShapefile(activeRegions.value.filter(region => region.enabled).map(region => regionPayload(region as LiveRegion)));
      regionExportNotice.value = result.saved ? `已导出 ${result.region_count} 个面` : "已取消导出";
      regionExportPath.value = result.path;
    } catch (error) {
      errorMessage.value = readableError(error);
    } finally {
      exportingRegions.value = false;
    }
  };

  const importRegions = async () => {
    if (pathMode.value) return;
    if (!sdkReady.value || selectionLocked.value || drawing.value) return;
    importingRegions.value = true;
    regionExportNotice.value = "";
    regionExportPath.value = null;
    errorMessage.value = null;
    try {
      regionExportNotice.value = "正在读取并校验 SHP";
      const result = await importRegionsShapefile();
      if (!result.imported) {
        regionExportNotice.value = "已取消导入";
        return;
      }
      const total = result.region_count ?? result.regions?.length ?? 0;
      if (stopped) return;
      let previewFailed = false;
      if (result.dataset_id) {
        previewFailed = await appendRegisteredRegions(result.dataset_id, total, {
          onPage: added => { regionExportNotice.value = `正在显示选区 ${added} / ${total}`; },
        });
      } else {
        const incoming = result.regions ?? [];
        assertRegionBudget([...regions.value, ...incoming]);
        let sliceStart = Date.now();
        for (const [i, region] of incoming.entries()) {
          if (stopped) return;
          let visible = false;
          try { sdk.showRegion(region); visible = true; } catch { previewFailed = true; }
          regions.value.push({ ...region, enabled: true, visible });
          if (Date.now() - sliceStart > 40) {
            regionExportNotice.value = `正在显示选区 ${i + 1} / ${total}`;
            await new Promise(resolve => window.setTimeout(resolve, 0));
            sliceStart = Date.now();
          }
        }
        if (incoming[0]) void revealRegion(geometryId(incoming[0]));
      }
      if (stopped) return;
      regionExportNotice.value = `已导入 ${total} 个面`;
      regionExportPath.value = result.source_name;
      if (previewFailed) errorMessage.value = "选区已导入，部分贴地预览未能显示，可点击选区的显示按钮重试";
    } catch (error) {
      if (stopped) return;
      regionExportNotice.value = "";
      errorMessage.value = readableError(error);
    } finally {
      importingRegions.value = false;
    }
  };

  const startDrawing = () => {
    if (!sdkReady.value || selectionLocked.value || drawing.value) return;
    errorMessage.value = null;
    try { sdk.beginDrawing(inputGeometry.value); drawing.value = true; }
    catch (error) { errorMessage.value = readableError(error); }
  };

  const showDraft = (region: typeof regions.value[number]) => {
    if (region.dataset_id && !sdk.hasRegion(geometryId(region))) {
      return regionGeometryPage(region.dataset_id, region.ordinal!, true).then(page => {
        const full = page.regions[0];
        if (!full || full.region_id !== region.region_id) throw new Error("选区草稿已失效，请重新导入或提取。");
        if (!stopped && regions.value.some(r => geometryId(r) === full.region_id)) {
          sdk.showRegion(full);
          if (!region.enabled) sdk.setRegionSelectionAppearance(geometryId(region), false);
        }
      });
    }
    sdk.showRegion(region);
    if (!region.enabled) sdk.setRegionSelectionAppearance(geometryId(region), false);
  };

  const toggleRegionPreview = async (region: typeof regions.value[number]) => {
    try {
      if (region.visible) sdk.hideRegion(geometryId(region));
      else { const loading = showDraft(region); if (loading) await loading; }
      region.visible = !region.visible;
    } catch (error) { errorMessage.value = readableError(error); }
  };

  const toggleAllRegionPreviews = async () => {
    if (!sdkReady.value || selectionLocked.value || drawing.value || !regions.value.length) return;
    const visible = !hasVisibleRegions.value;
    updatingRegionVisibility.value = true;
    errorMessage.value = null;
    let failures = 0;
    try {
      for (let i = 0; i < activeRegions.value.length; i++) {
        if (stopped) break;
        const region = activeRegions.value[i]!;
        if (region.visible !== visible) {
          try {
            if (visible) { const loading = showDraft(region); if (loading) await loading; }
            else sdk.hideRegion(geometryId(region));
            region.visible = visible;
          } catch { failures++; }
        }
        if (i % 20 === 19) await new Promise(resolve => window.setTimeout(resolve, 0));
      }
      if (failures && !stopped) errorMessage.value = `${failures} 个选区预览未能${visible ? "显示" : "隐藏"}，请重试`;
    } finally {
      updatingRegionVisibility.value = false;
    }
  };

  const locateRegion = (region: LiveGeometry) => {
    try { sdk.focusRegion(region); }
    catch (error) { errorMessage.value = readableError(error); }
  };

  const removeRegion = (region: LiveGeometry) => {
    if (selectionLocked.value) return;
    try {
      sdk.removeRegion(geometryId(region));
      regions.value = regions.value.filter(item => geometryId(item) !== geometryId(region));
    } catch (error) { errorMessage.value = readableError(error); }
  };
  const addDrawnRegion = (region: LiveGeometry) => {
    try { assertRegionBudget([...regions.value, region]); }
    catch (error) { errorMessage.value = readableError(error); return; }
    regions.value.push({ ...region, enabled: true, visible: false });
    toggleRegionPreview(regions.value.at(-1)!);
    void revealRegion(geometryId(region));
  };
  const restoreRegions = (value: LiveGeometry[]) => {
    regions.value = value.map(region => ({ ...region, enabled: true, visible: false }));
  };
  const completeRegions = () => {
    let failures = 0;
    for (const region of activeRegions.value) {
      if (!region.enabled || failureByRegion.value.has(geometryId(region))) continue;
      setRegionEnabled(region, false);
      try {
        sdk.hideRegion(geometryId(region));
        region.visible = false;
      } catch { failures++; }
    }
    if (failures) errorMessage.value = `${failures} 个已完成选区的预览未能隐藏，已取消勾选，可手动隐藏`;
  };
  watch(regionSearch, () => { regionPage.value = 0; });
  watch(inputGeometry, () => {
    if (drawing.value) { sdk.cancelDrawing(); drawing.value = false; }
    regionSearch.value = "";
    regionPage.value = 0;
    for (const region of regions.value) {
      if (geometryKind(region) !== inputGeometry.value && region.visible) {
        try { sdk.hideRegion(geometryId(region)); region.visible = false; }
        catch (error) { errorMessage.value = readableError(error); }
      }
    }
  });
  watch(regionPageCount, count => { regionPage.value = Math.min(regionPage.value, count - 1); });
  onBeforeUnmount(() => { stopped = true; });
  const cancelDrawing = () => sdk.cancelDrawing();
  return {
    addDrawnRegion,
    restoreRegions,
    completeRegions,
    cancelDrawing,
    samplingFailures,
    drawing,
    exportingRegions,
    importingRegions,
    clearingRegions,
    updatingRegionVisibility,
    clearRegionsDialog,
    regionExportNotice,
    regionExportPath,
    regions: activeRegions,
    pathMode,
    regionSearch,
    regionPage,
    focusedRegionId,
    filteredRegions,
    regionPageCount,
    visibleRegions,
    failureByRegion,
    revealRegion,
    selectFailedRegions,
    selectionLocked,
    actorPicking,
    regionBrush,
    setRegionEnabled,
    enabledRegionCount,
    hasVisibleRegions,
    selectAllRegions,
    requestClearRegions,
    clearRegions,
    exportRegions,
    importRegions,
    startDrawing,
    toggleRegionPreview,
    toggleAllRegionPreviews,
    locateRegion,
    removeRegion,
  };
}
