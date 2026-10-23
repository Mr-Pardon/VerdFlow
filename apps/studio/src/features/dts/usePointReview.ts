import { computed, onBeforeUnmount, ref, shallowRef } from "vue";

import { LocalApiClient, LocalApiError } from "../../api";
import type {
  Bounds3D,
  PointReviewItem,
  PointReviewManifest,
  RegionViewerItem,
  RegionViewerManifest,
} from "../../api";
import { platformBridge } from "../../platform";

const ASSET_COLORS = [
  "#df5b4d",
  "#2f897e",
  "#d69b32",
  "#4f78b8",
  "#a45c95",
  "#6f8f3e",
  "#bd6b32",
  "#477f97",
];
const REGION_PAGE_SIZE = 5000;
const MAX_REGION_PAGES = 4;
const POINT_DETAIL_ENTER_LIMIT = 35_000;
const POINT_DETAIL_EXIT_LIMIT = 50_000;
const MAX_VIEWPORT_POINT_ITEMS = 50_000;
const POINT_PAGE_SIZE = 5000;

export interface PointReviewSource {
  key: string;
  pointSetId: string;
  assignmentSetId: string;
}

export const usePointReview = (input: {
  sessionId: string;
  sources: PointReviewSource[];
}) => {
  const client = new LocalApiClient(platformBridge());
  const manifests = shallowRef<Record<string, PointReviewManifest>>({});
  const regionManifest = shallowRef<RegionViewerManifest | null>(null);
  const pointsBySource = shallowRef<Record<string, PointReviewItem[]>>({});
  const regions = shallowRef<RegionViewerItem[]>([]);
  const loading = ref(true);
  const loadingPoints = ref(false);
  const loadingRegions = ref(false);
  const regionsCapped = ref(false);
  const pointDetailsVisible = ref(false);
  const pointsCapped = ref(false);
  const errorMessage = ref<string | null>(null);
  let viewportRevision = 0;
  let disposed = false;
  let pendingViewport: { bounds: Bounds3D; revision: number } | null = null;
  let viewportRequest: Promise<void> | undefined;
  let lastViewport: Bounds3D | null = null;

  const colors = computed(() => {
    const keys = input.sources.flatMap(
      (source) =>
        manifests.value[source.key]?.assets.map((asset) => asset.key) ?? [],
    );
    return Object.fromEntries(
      [...new Set(keys)].map((key, index) => [
        key,
        ASSET_COLORS[index % ASSET_COLORS.length],
      ]),
    );
  });
  const maxResolvedRadius = computed(() =>
    Math.max(
      0,
      ...Object.values(manifests.value).map(
        (manifest) => manifest.max_resolved_radius_m ?? 0,
      ),
    ),
  );
  const reviewBounds = computed(() => {
    const available = [
      regionManifest.value?.bounds ?? null,
      ...Object.values(manifests.value).map((manifest) => manifest.bounds),
    ].filter((bounds): bounds is Bounds3D => bounds !== null);
    if (!available.length) return null;
    const margin = maxResolvedRadius.value;
    return {
      min_x: Math.min(...available.map((bounds) => bounds.min_x)) - margin,
      min_y: Math.min(...available.map((bounds) => bounds.min_y)) - margin,
      min_z: Math.min(...available.map((bounds) => bounds.min_z)),
      max_x: Math.max(...available.map((bounds) => bounds.max_x)) + margin,
      max_y: Math.max(...available.map((bounds) => bounds.max_y)) + margin,
      max_z: Math.max(...available.map((bounds) => bounds.max_z)),
    };
  });

  const initialize = async () => {
    if (disposed) return;
    loading.value = true;
    errorMessage.value = null;
    try {
      if (!input.sources.length) {
        throw new Error("点位预览没有可读取的批次");
      }
      const entries = await Promise.all(
        input.sources.map(
          async (source) =>
            [
              source.key,
              await client.pointReviewManifest({
                sessionId: input.sessionId,
                pointSetId: source.pointSetId,
                assignmentSetId: source.assignmentSetId,
              }),
            ] as const,
        ),
      );
      const nextManifests = Object.fromEntries(entries);
      if (disposed) return;
      const regionSetIds = new Set(
        Object.values(nextManifests).map((manifest) => manifest.region_set_id),
      );
      if (regionSetIds.size !== 1 || regionSetIds.has("")) {
        throw new Error("点位批次没有引用同一个有效 RegionSet");
      }
      manifests.value = nextManifests;
      const nextRegionManifest = await client.regionManifest(
        input.sessionId,
        entries[0][1].region_set_id,
      );
      if (!disposed) regionManifest.value = nextRegionManifest;
    } catch (error) {
      if (!disposed) errorMessage.value = readableError(error);
    } finally {
      if (!disposed) loading.value = false;
    }
  };

  const readViewport = async (bounds: Bounds3D, revision: number) => {
    const estimates = input.sources.map((source) => ({
      source,
      pointCount: estimateVisiblePointCount(source, bounds),
    }));
    const estimatedPointCount = estimates.reduce(
      (total, item) => total + item.pointCount,
      0,
    );
    const detailLimit = pointDetailsVisible.value
      ? POINT_DETAIL_EXIT_LIMIT
      : POINT_DETAIL_ENTER_LIMIT;
    const loadPointDetails = estimatedPointCount <= detailLimit;
    pointDetailsVisible.value = loadPointDetails;
    pointsCapped.value = false;
    loadingPoints.value = loadPointDetails;
    loadingRegions.value = true;
    errorMessage.value = null;
    if (!loadPointDetails) pointsBySource.value = {};
    try {
      const pointBounds = expandBounds(bounds, maxResolvedRadius.value);
      const weightTotal = estimates.reduce(
        (total, item) => total + Math.max(0, item.pointCount),
        0,
      );
      const [pointEntries, terrain] = await Promise.all([
        loadPointDetails
          ? Promise.all(
              estimates.map(async ({ source, pointCount }) => {
                const budget = weightTotal && pointCount > 0
                  ? Math.max(
                      1,
                      Math.floor(
                        MAX_VIEWPORT_POINT_ITEMS * pointCount / weightTotal,
                      ),
                    )
                  : 0;
                return [
                  source.key,
                  await loadPointsForSource(
                    source,
                    pointBounds,
                    revision,
                    budget,
                  ),
                ] as const;
              }),
            )
          : Promise.resolve([]),
        loadRegions(bounds, revision),
      ]);
      if (revision !== viewportRevision) return;
      if (loadPointDetails) {
        pointsBySource.value = Object.fromEntries(
          pointEntries.map(([key, result]) => [key, result.items]),
        );
        pointsCapped.value = pointEntries.some(([, result]) => result.capped);
      }
      regions.value = terrain.items;
      regionsCapped.value = terrain.capped;
    } catch (error) {
      if (revision === viewportRevision) {
        errorMessage.value = readableError(error);
      }
    } finally {
      if (revision === viewportRevision) loadingPoints.value = false;
      if (revision === viewportRevision) loadingRegions.value = false;
    }
  };

  const drainViewports = async () => {
    try {
      while (pendingViewport && !disposed) {
        const { bounds, revision } = pendingViewport;
        pendingViewport = null;
        await readViewport(bounds, revision);
      }
    } finally {
      viewportRequest = undefined;
    }
  };

  const loadViewport = (bounds: Bounds3D) => {
    if (disposed) return Promise.resolve();
    lastViewport = bounds;
    // Let the current request finish, retaining only the most recent camera view.
    pendingViewport = { bounds, revision: ++viewportRevision };
    viewportRequest ??= drainViewports();
    return viewportRequest;
  };

  const retry = async () => {
    if (disposed) return;
    if (!regionManifest.value) await initialize();
    if (regionManifest.value && lastViewport && !disposed) {
      await loadViewport(lastViewport);
    }
  };

  const loadPointsForSource = async (
    source: PointReviewSource,
    bounds: Bounds3D,
    revision: number,
    maxItems: number,
  ) => {
    const collected: PointReviewItem[] = [];
    if (maxItems <= 0) return { items: collected, capped: false };
    let afterOrdinal = -1;
    for (;;) {
      const remaining = maxItems - collected.length;
      if (remaining <= 0) {
        return { items: collected, capped: true };
      }
      const chunk = await client.pointReviewPoints({
        sessionId: input.sessionId,
        pointSetId: source.pointSetId,
        assignmentSetId: source.assignmentSetId,
        minX: bounds.min_x,
        minY: bounds.min_y,
        maxX: bounds.max_x,
        maxY: bounds.max_y,
        afterOrdinal,
        limit: Math.min(POINT_PAGE_SIZE, remaining),
      });
      if (revision !== viewportRevision) {
        return { items: [], capped: false };
      }
      collected.push(...chunk.items);
      if (!chunk.truncated || chunk.next_after_ordinal === null) break;
      afterOrdinal = chunk.next_after_ordinal;
    }
    return { items: collected, capped: false };
  };

  const estimateVisiblePointCount = (
    source: PointReviewSource,
    bounds: Bounds3D,
  ) => {
    const manifest = manifests.value[source.key];
    if (!manifest?.bounds) return manifest?.point_count ?? 0;
    const viewport = expandBounds(bounds, maxResolvedRadius.value);
    if (
      viewport.max_x < manifest.bounds.min_x || viewport.min_x > manifest.bounds.max_x ||
      viewport.max_y < manifest.bounds.min_y || viewport.min_y > manifest.bounds.max_y
    ) return 0;
    const sourceWidth = Math.max(
      manifest.bounds.max_x - manifest.bounds.min_x,
      1e-9,
    );
    const sourceHeight = Math.max(
      manifest.bounds.max_y - manifest.bounds.min_y,
      1e-9,
    );
    const visibleWidth = manifest.bounds.max_x === manifest.bounds.min_x
      ? sourceWidth
      : Math.max(
          0,
          Math.min(viewport.max_x, manifest.bounds.max_x)
            - Math.max(viewport.min_x, manifest.bounds.min_x),
        );
    const visibleHeight = manifest.bounds.max_y === manifest.bounds.min_y
      ? sourceHeight
      : Math.max(
          0,
          Math.min(viewport.max_y, manifest.bounds.max_y)
            - Math.max(viewport.min_y, manifest.bounds.min_y),
        );
    return Math.ceil(
      manifest.point_count
        * Math.min(1, visibleWidth * visibleHeight / (sourceWidth * sourceHeight)),
    );
  };

  const loadRegions = async (bounds: Bounds3D, revision: number) => {
    const manifest = Object.values(manifests.value)[0];
    if (!manifest) return { items: [], capped: false };
    const collected: RegionViewerItem[] = [];
    let afterOrdinal = -1;
    let capped = false;
    for (let page = 0; page < MAX_REGION_PAGES; page += 1) {
      const chunk = await client.regionGeometry({
        sessionId: input.sessionId,
        regionSetId: manifest.region_set_id,
        minX: bounds.min_x,
        minY: bounds.min_y,
        maxX: bounds.max_x,
        maxY: bounds.max_y,
        afterOrdinal,
        limit: REGION_PAGE_SIZE,
      });
      if (revision !== viewportRevision) {
        return { items: [], capped: false };
      }
      collected.push(...chunk.items);
      if (!chunk.truncated || chunk.next_after_ordinal === null) break;
      afterOrdinal = chunk.next_after_ordinal;
      capped = page === MAX_REGION_PAGES - 1;
    }
    return { items: collected, capped };
  };

  onBeforeUnmount(() => {
    disposed = true;
    viewportRevision += 1;
    pendingViewport = null;
  });

  return {
    colors,
    errorMessage,
    initialize,
    loading,
    loadingPoints,
    loadingRegions,
    loadViewport,
    maxResolvedRadius,
    manifests,
    pointDetailsVisible,
    pointsCapped,
    pointsBySource,
    regionManifest,
    reviewBounds,
    retry,
    regions,
    regionsCapped,
  };
};

const expandBounds = (bounds: Bounds3D, margin: number): Bounds3D => ({
  min_x: bounds.min_x - margin,
  min_y: bounds.min_y - margin,
  min_z: bounds.min_z,
  max_x: bounds.max_x + margin,
  max_y: bounds.max_y + margin,
  max_z: bounds.max_z,
});

const readableError = (error: unknown) => {
  if (error instanceof LocalApiError) return error.code === "connection_failed"
    ? "点位预览请求未完成，本地服务连接暂时失败"
    : error.message;
  return error instanceof Error ? error.message : "点位预览加载失败";
};
