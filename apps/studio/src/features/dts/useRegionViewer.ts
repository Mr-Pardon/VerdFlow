import { computed, onBeforeUnmount, ref, shallowRef } from "vue";

import { LocalApiClient, LocalApiError } from "../../api";
import type {
  Bounds3D,
  RegionViewerItem,
  RegionViewerManifest,
} from "../../api";
import { platformBridge } from "../../platform";
import { readRegionViewport } from "./regionViewport";
import type { RegionOverviewGrid } from "./regionViewport";

export type RegionSelectionMode = "replace" | "add" | "toggle";

export const useRegionViewer = (sessionId: string, regionSetId: string) => {
  const client = new LocalApiClient(platformBridge());
  const manifest = shallowRef<RegionViewerManifest | null>(null);
  const regions = shallowRef<RegionViewerItem[]>([]);
  const overview = shallowRef<RegionOverviewGrid | null>(null);
  const selected = ref<Set<string>>(new Set());
  const pendingClick = shallowRef<{ id: string; mode: RegionSelectionMode } | null>(null);
  const classifications = ref<string[]>([]);
  const loadingManifest = ref(true);
  const loadingGeometry = ref(false);
  const selecting = ref(false);
  const errorMessage = ref<string | null>(null);
  const currentViewport = shallowRef<Bounds3D | null>(null);
  let viewportSequence = 0;
  let pendingViewport: { bounds: Bounds3D; sequence: number } | null = null;
  let viewportRequest: Promise<void> | undefined;
  let selectionSequence = 0;

  const selectedRegionIds = computed(() => [...selected.value]);
  const displaySelectedRegionIds = computed(() => {
    const pending = pendingClick.value;
    return pending ? [...mergeSelection(selected.value, [pending.id], pending.mode)] : selectedRegionIds.value;
  });
  const exactSelection = computed(() => overview.value !== null ||
    regions.value.some(item => item.boundary_mode && item.boundary_mode !== "detail"));

  const invalidateSelection = () => {
    selectionSequence += 1;
    pendingClick.value = null;
    selecting.value = false;
  };

  onBeforeUnmount(() => {
    viewportSequence += 1;
    pendingViewport = null;
    invalidateSelection();
  });

  const initialize = async () => {
    loadingManifest.value = true;
    errorMessage.value = null;
    try {
      manifest.value = await client.regionManifest(sessionId, regionSetId);
    } catch (error) {
      errorMessage.value = readableError(error);
    } finally {
      loadingManifest.value = false;
    }
  };

  const readViewport = async (bounds: Bounds3D, sequence: number) => {
    loadingGeometry.value = true;
    errorMessage.value = null;
    try {
      const view = await readRegionViewport({
        client, sessionId, regionSetId, bounds, classifications: classifications.value,
        isCurrent: () => sequence === viewportSequence,
      });
      if (!view || sequence !== viewportSequence) return;
      regions.value = view.items;
      overview.value = view.overview;
    } catch (error) {
      if (sequence === viewportSequence) {
        errorMessage.value = readableError(error);
      }
    } finally {
      if (sequence === viewportSequence) {
        loadingGeometry.value = false;
      }
    }
  };

  const drainViewports = async () => {
    try {
      while (pendingViewport) {
        const { bounds, sequence } = pendingViewport;
        pendingViewport = null;
        await readViewport(bounds, sequence);
      }
    } finally {
      viewportRequest = undefined;
    }
  };

  const loadViewport = (bounds: Bounds3D) => {
    currentViewport.value = bounds;
    pendingViewport = { bounds, sequence: ++viewportSequence };
    viewportRequest ??= drainViewports();
    return viewportRequest;
  };

  const setClassificationFilter = async (values: string[]) => {
    invalidateSelection();
    classifications.value = [...values];
    if (currentViewport.value) {
      await loadViewport(currentViewport.value);
    }
  };

  const applySelection = (ids: string[], mode: RegionSelectionMode) => {
    invalidateSelection();
    selected.value = mergeSelection(selected.value, ids, mode);
  };

  const selectByGeometry = async (
    corners: readonly { x: number; y: number }[],
    mode: RegionSelectionMode,
    committed: () => ReadonlySet<string>,
    previewId?: string,
  ) => {
    const sequence = ++selectionSequence;
    selecting.value = true;
    pendingClick.value = previewId && !committed().has(previewId) ? { id: previewId, mode } : null;
    errorMessage.value = null;
    try {
      const ids: string[] = [];
      let afterOrdinal = -1;
      while (true) {
        const page = await client.regionSelection({
          sessionId, regionSetId, corners, classifications: classifications.value, afterOrdinal,
        });
        if (sequence !== selectionSequence) return;
        ids.push(...page.region_ids);
        if (page.next_after_ordinal === null) break;
        if (page.next_after_ordinal <= afterOrdinal) throw new Error("区域选择响应顺序无效");
        afterOrdinal = page.next_after_ordinal;
      }
      const excluded = committed();
      applySelection(ids.filter(id => !excluded.has(id)), mode);
    } catch (error) {
      if (sequence === selectionSequence) errorMessage.value = readableError(error);
    } finally {
      if (sequence === selectionSequence) {
        pendingClick.value = null;
        selecting.value = false;
      }
    }
  };

  const clearSelection = () => {
    applySelection([], "replace");
  };

  return {
    applySelection,
    classifications,
    clearSelection,
    displaySelectedRegionIds,
    errorMessage,
    exactSelection,
    initialize,
    loadingGeometry,
    loadingManifest,
    loadViewport,
    manifest,
    overview,
    regions,
    selectByGeometry,
    selecting,
    selectedRegionIds,
    setClassificationFilter,
  };
};

function mergeSelection(current: ReadonlySet<string>, ids: string[], mode: RegionSelectionMode) {
  if (mode === "replace") return new Set(ids);
  const next = new Set(current);
  for (const id of ids) {
    if (mode === "toggle" && next.has(id)) next.delete(id);
    else next.add(id);
  }
  return next;
}

const readableError = (error: unknown): string => {
  if (error instanceof LocalApiError) {
    return error.code === "connection_failed"
      ? "无法连接 Region Viewer 服务"
      : error.message;
  }
  return error instanceof Error ? error.message : "Region Viewer 读取失败";
};
