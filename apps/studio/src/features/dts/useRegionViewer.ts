import { computed, ref, shallowRef } from "vue";

import { LocalApiClient, LocalApiError } from "../../api";
import type {
  Bounds3D,
  RegionViewerItem,
  RegionViewerManifest,
} from "../../api";
import { platformBridge } from "../../platform";

export type RegionSelectionMode = "replace" | "add" | "toggle";

const PAGE_SIZE = 5000;
const MAX_VIEWPORT_PAGES = 4;

export const useRegionViewer = (sessionId: string, regionSetId: string) => {
  const client = new LocalApiClient(platformBridge());
  const manifest = shallowRef<RegionViewerManifest | null>(null);
  const regions = shallowRef<RegionViewerItem[]>([]);
  const selected = ref<Set<string>>(new Set());
  const classifications = ref<string[]>([]);
  const loadingManifest = ref(true);
  const loadingGeometry = ref(false);
  const geometryCapped = ref(false);
  const errorMessage = ref<string | null>(null);
  const currentViewport = shallowRef<Bounds3D | null>(null);
  let viewportSequence = 0;

  const selectedRegionIds = computed(() => [...selected.value]);

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

  const loadViewport = async (bounds: Bounds3D) => {
    const sequence = ++viewportSequence;
    currentViewport.value = bounds;
    loadingGeometry.value = true;
    errorMessage.value = null;
    try {
      const nextRegions: RegionViewerItem[] = [];
      let afterOrdinal = -1;
      let capped = false;
      for (let page = 0; page < MAX_VIEWPORT_PAGES; page += 1) {
        const chunk = await client.regionGeometry({
          sessionId,
          regionSetId,
          minX: bounds.min_x,
          minY: bounds.min_y,
          maxX: bounds.max_x,
          maxY: bounds.max_y,
          classifications: classifications.value,
          afterOrdinal,
          limit: PAGE_SIZE,
        });
        if (sequence !== viewportSequence) return;
        nextRegions.push(...chunk.items);
        if (!chunk.truncated || chunk.next_after_ordinal === null) break;
        afterOrdinal = chunk.next_after_ordinal;
        capped = page === MAX_VIEWPORT_PAGES - 1;
      }
      if (sequence !== viewportSequence) return;
      regions.value = nextRegions;
      geometryCapped.value = capped;
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

  const setClassificationFilter = async (values: string[]) => {
    classifications.value = [...values];
    if (currentViewport.value) {
      await loadViewport(currentViewport.value);
    }
  };

  const applySelection = (ids: string[], mode: RegionSelectionMode) => {
    if (mode === "replace") {
      selected.value = new Set(ids);
      return;
    }
    const next = new Set(selected.value);
    for (const id of ids) {
      if (mode === "toggle" && next.has(id)) next.delete(id);
      else next.add(id);
    }
    selected.value = next;
  };

  const clearSelection = () => {
    selected.value = new Set();
  };

  return {
    applySelection,
    classifications,
    clearSelection,
    errorMessage,
    geometryCapped,
    initialize,
    loadingGeometry,
    loadingManifest,
    loadViewport,
    manifest,
    regions,
    selectedRegionIds,
    setClassificationFilter,
  };
};

const readableError = (error: unknown): string => {
  if (error instanceof LocalApiError) {
    return error.code === "connection_failed"
      ? "无法连接 Region Viewer 服务"
      : error.message;
  }
  return error instanceof Error ? error.message : "Region Viewer 读取失败";
};
