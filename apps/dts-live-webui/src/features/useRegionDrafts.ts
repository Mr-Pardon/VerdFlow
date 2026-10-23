import { ref, computed, nextTick, watch, onBeforeUnmount } from "vue";
import { type LiveRegion } from "../types";
import { exportRegionsShapefile, importRegionsShapefile } from "../api";
import type { Ref } from "vue";
import type { DtsSdkBridge } from "../sdk/DtsSdkBridge";
import { readableError } from "../shared/presentation";
import type { SessionSnapshot } from "../types";

export function useRegionDrafts(context: {
  samplingFailures: Ref<NonNullable<SessionSnapshot["sampling_failures"]>>;
  panelCollapsed: Ref<boolean>;
  operationActive: Ref<boolean>;
  submitting: Ref<boolean>;
  errorMessage: Ref<string | null>;
  sdk: DtsSdkBridge;
  sdkReady: Ref<boolean>
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

  const regions = ref<(LiveRegion & { enabled: boolean; visible: boolean })[]>([]);

  const regionSearch = ref("");

  const regionPage = ref(0);

  const focusedRegionId = ref("");

  const REGION_PAGE_SIZE = 50;

  const filteredRegions = computed(() => {
    const query = regionSearch.value.trim().toLocaleLowerCase();
    return regions.value.filter((region, i) => !query || region.name.toLocaleLowerCase().includes(query) || String(i + 1) === query);
  });

  const regionPageCount = computed(() => Math.max(1, Math.ceil(filteredRegions.value.length / REGION_PAGE_SIZE)));

  const visibleRegions = computed(() => filteredRegions.value.slice(regionPage.value * REGION_PAGE_SIZE, (regionPage.value + 1) * REGION_PAGE_SIZE));

  const failureByRegion = computed(() => new Map(samplingFailures.value.map(item => [item.region_id, item.reason])));

  const revealRegion = async (regionId: string) => {
    const index = regions.value.findIndex(region => region.region_id === regionId);
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
    for (const region of regions.value) region.enabled = failureByRegion.value.has(region.region_id);
    const first = samplingFailures.value[0];
    if (first) void revealRegion(first.region_id);
  };

  const selectionLocked = computed(() => operationActive.value || submitting.value || exportingRegions.value || importingRegions.value || clearingRegions.value || updatingRegionVisibility.value);

  const enabledRegionCount = computed(() => regions.value.filter(region => region.enabled).length);

  const hasVisibleRegions = computed(() => regions.value.some(region => region.visible));

  const selectAllRegions = (invert = false) => {
    if (selectionLocked.value || drawing.value) return;
    for (const region of regions.value) region.enabled = invert ? !region.enabled : true;
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
      for (let i = 0; i < regions.value.length; i++) {
        const region = regions.value[i]!;
        try { sdk.hideRegion(region.region_id); }
        catch { remaining.push(region); }
        if (i % 20 === 19) await new Promise(resolve => window.setTimeout(resolve, 0));
      }
      regions.value = remaining;
      regionSearch.value = "";
      regionPage.value = 0;
      focusedRegionId.value = "";
      if (remaining.length) errorMessage.value = `${remaining.length} 个选区预览未能清除，已保留在列表中，请重试`;
    } finally {
      clearingRegions.value = false;
    }
  };

  const exportRegions = async () => {
    if (selectionLocked.value || drawing.value || !enabledRegionCount.value) return;
    exportingRegions.value = true;
    regionExportNotice.value = "";
    regionExportPath.value = null;
    errorMessage.value = null;
    try {
      const result = await exportRegionsShapefile(regions.value.filter(region => region.enabled).map(
        ({ region_id, name, coordinates, bbox }) => ({ region_id, name, coordinates, bbox }),
      ));
      regionExportNotice.value = result.saved ? `已导出 ${result.region_count} 个面` : "已取消导出";
      regionExportPath.value = result.path;
    } catch (error) {
      errorMessage.value = readableError(error);
    } finally {
      exportingRegions.value = false;
    }
  };

  const importRegions = async () => {
    if (!sdkReady.value || selectionLocked.value || drawing.value) return;
    importingRegions.value = true;
    regionExportNotice.value = "";
    regionExportPath.value = null;
    errorMessage.value = null;
    try {
      const result = await importRegionsShapefile();
      if (!result.imported) {
        regionExportNotice.value = "已取消导入";
        return;
      }
      const combined = [...regions.value, ...result.regions].map(
        ({ region_id, name, coordinates, bbox }) => ({ region_id, name, coordinates, bbox }),
      );
      if (new TextEncoder().encode(JSON.stringify(combined)).byteLength > 30 * 1024 * 1024) {
        throw new Error("合并后的选区数据过大，请减少现有选区或简化导入轮廓；本次未追加选区");
      }
      const added = result.regions.map(region => ({ ...region, enabled: true, visible: false }));
      regions.value.push(...added);
      let previewFailed = false;
      let previewCount = 0;
      for (const region of regions.value.slice(-added.length)) {
        if (stopped) break;
        try {
          sdk.showRegion(region);
          region.visible = true;
        } catch { previewFailed = true; }
        previewCount++;
        if (previewCount % 20 === 0) {
          regionExportNotice.value = `正在显示选区 ${previewCount} / ${added.length}`;
          await new Promise(resolve => window.setTimeout(resolve, 0));
        }
      }
      regionExportNotice.value = `已导入 ${added.length} 个面`;
      regionExportPath.value = result.source_name;
      if (added[0]) void revealRegion(added[0].region_id);
      if (previewFailed) errorMessage.value = "选区已导入，部分贴地预览未能显示，可点击选区的显示按钮重试";
    } catch (error) {
      errorMessage.value = readableError(error);
    } finally {
      importingRegions.value = false;
    }
  };

  const startDrawing = () => {
    if (!sdkReady.value || selectionLocked.value || drawing.value) return;
    errorMessage.value = null;
    try { sdk.beginDrawing(); drawing.value = true; }
    catch (error) { errorMessage.value = readableError(error); }
  };

  const toggleRegionPreview = (region: typeof regions.value[number]) => {
    try {
      if (region.visible) sdk.hideRegion(region.region_id);
      else sdk.showRegion(region);
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
      for (let i = 0; i < regions.value.length; i++) {
        if (stopped) break;
        const region = regions.value[i]!;
        if (region.visible !== visible) {
          try {
            if (visible) sdk.showRegion(region);
            else sdk.hideRegion(region.region_id);
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

  const locateRegion = (region: LiveRegion) => {
    try { sdk.focusRegion(region); }
    catch (error) { errorMessage.value = readableError(error); }
  };

  const removeRegion = (region: LiveRegion) => {
    if (selectionLocked.value) return;
    try {
      sdk.hideRegion(region.region_id);
      regions.value = regions.value.filter(item => item.region_id !== region.region_id);
    } catch (error) { errorMessage.value = readableError(error); }
  };
  const addDrawnRegion = (region: LiveRegion) => {
    regions.value.push({ ...region, enabled: true, visible: false });
    toggleRegionPreview(regions.value.at(-1)!);
    void revealRegion(region.region_id);
  };
  const restoreRegions = (value: LiveRegion[]) => {
    regions.value = value.map(region => ({ ...region, enabled: true, visible: false }));
  };
  const completeRegions = () => {
    let failures = 0;
    for (const region of regions.value) {
      if (!region.enabled || failureByRegion.value.has(region.region_id)) continue;
      region.enabled = false;
      try {
        sdk.hideRegion(region.region_id);
        region.visible = false;
      } catch { failures++; }
    }
    if (failures) errorMessage.value = `${failures} 个已完成选区的预览未能隐藏，已取消勾选，可手动隐藏`;
  };
  watch(regionSearch, () => { regionPage.value = 0; });
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
    regions,
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
