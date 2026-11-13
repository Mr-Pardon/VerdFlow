<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";

import type { Bounds3D, RegionViewerItem } from "../../api";
import {
  boundaryIntersectsRectangle,
  boundsContainPoint,
  extentBoundary,
  pointInBoundary,
} from "./regionGeometry";
import type { RegionSelectionMode } from "./useRegionViewer";
import type { ImageryDetailTile } from "./imageryDetail";
import { createRegionOverviewCanvas, drawRegionOverview, pickRegionOverview } from "./regionViewport";
import type { RegionOverviewGrid } from "./regionViewport";

const props = defineProps<{
  bounds: Bounds3D | null;
  regions: RegionViewerItem[];
  regionOverview?: RegionOverviewGrid | null;
  selectedRegionIds: string[];
  committedRegionIds: string[];
  loading: boolean;
  exactSelection?: boolean;
  backdrop?: { url: string; transform: [number, number, number, number, number, number] } | null;
  overview?: {
    canvas: HTMLCanvasElement;
    transform: [number, number, number, number, number, number];
    pick: (x: number, y: number) => string | undefined;
    pickArea: (corners: readonly { x: number; y: number }[], covered?: (x: number, y: number) => boolean) => string[];
  } | null;
  imageryReview?: boolean;
  imageryTiles?: ImageryDetailTile[];
}>();

const emit = defineEmits<{
  viewChanging: [];
  viewport: [bounds: Bounds3D, pixelsPerMeter: number];
  selection: [payload: { ids: string[]; mode: RegionSelectionMode }];
  selectionQuery: [corners: { x: number; y: number }[], mode: RegionSelectionMode, previewId?: string];
}>();

const canvas = ref<HTMLCanvasElement | null>(null);
const view = reactive({
  width: 1,
  height: 1,
  centerX: 0,
  centerY: 0,
  scale: 1,
  rotation: 0,
  initialized: false,
});
let resizeObserver: ResizeObserver | undefined;
let renderFrame: number | undefined;
let viewportTimer: ReturnType<typeof setTimeout> | undefined;
let backdropImage: HTMLImageElement | undefined;
let viewAdjusted = false;
let focusedRegion: Bounds3D | null = null;
let drag:
  | {
      kind: "pan" | "rotate" | "selection";
      startX: number;
      startY: number;
      currentX: number;
      currentY: number;
      lastX: number;
      lastY: number;
      shiftKey: boolean;
      ctrlKey: boolean;
    }
  | undefined;

const selected = () => new Set(props.selectedRegionIds);
const committed = () => new Set(props.committedRegionIds);

const fitBounds = (bounds: Bounds3D, minimumSpan: number, adjusted: boolean) => {
  if (view.width <= 1 || view.height <= 1) return;
  const width = Math.max(bounds.max_x - bounds.min_x, minimumSpan);
  const height = Math.max(bounds.max_y - bounds.min_y, minimumSpan);
  const cosine = Math.abs(Math.cos(view.rotation));
  const sine = Math.abs(Math.sin(view.rotation));
  const projectedWidth = width * cosine + height * sine;
  const projectedHeight = width * sine + height * cosine;
  view.centerX = (bounds.min_x + bounds.max_x) / 2;
  view.centerY = (bounds.min_y + bounds.max_y) / 2;
  view.scale = Math.max(
    1e-6,
    Math.min(
      (view.width - 56) / projectedWidth,
      (view.height - 56) / projectedHeight,
    ),
  );
  view.initialized = true;
  viewAdjusted = adjusted;
  scheduleRender();
  scheduleViewport();
};

const fit = () => { if (props.bounds) fitBounds(props.bounds, 1e-6, false); };
const clearFocus = () => { focusedRegion = null; scheduleRender(); };
const focusRegion = (bounds: Bounds3D, minimumSpan: number) => {
  focusedRegion = bounds;
  fitBounds(bounds, minimumSpan, true);
};
const resetRotation = () => {
  clearFocus();
  view.rotation = 0;
  fit();
};

defineExpose({ fit, resetRotation, focusRegion, clearFocus });

const resize = () => {
  const element = canvas.value;
  if (!element) return;
  const rect = element.getBoundingClientRect();
  const nextWidth = Math.max(1, Math.round(rect.width));
  const nextHeight = Math.max(1, Math.round(rect.height));
  const changed = nextWidth !== view.width || nextHeight !== view.height;
  view.width = nextWidth;
  view.height = nextHeight;
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  element.width = Math.round(nextWidth * ratio);
  element.height = Math.round(nextHeight * ratio);
  if (changed && props.bounds && (!view.initialized || (props.imageryReview && !viewAdjusted))) fit();
  else {
    scheduleRender();
    if (changed && props.imageryReview) scheduleViewport();
  }
};

const scheduleRender = () => {
  if (renderFrame !== undefined) return;
  renderFrame = requestAnimationFrame(() => {
    renderFrame = undefined;
    render();
  });
};

const render = () => {
  const element = canvas.value;
  if (!element) return;
  const context = element.getContext("2d");
  if (!context) return;
  const ratio = element.width / view.width;
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, view.width, view.height);
  context.fillStyle = "#edf3f1";
  context.fillRect(0, 0, view.width, view.height);
  if (props.backdrop && backdropImage?.complete && backdropImage.naturalWidth) {
    drawRaster(context, backdropImage, props.backdrop.transform);
  }
  if (props.overview) drawRaster(context, props.overview.canvas, props.overview.transform);
  for (const tile of props.imageryTiles ?? []) {
    drawRaster(context, tile.backdrop, tile.transform);
    if (props.overview) drawRaster(context, tile.canvas, tile.transform);
  }

  const selectedIds = selected();
  const committedIds = committed();
  if (props.regionOverview && overviewCanvas.value) {
    drawRegionOverview(context, overviewCanvas.value, props.regionOverview.bounds, worldToScreen);
  }
  for (const region of props.regions) {
    drawRegion(
      context,
      region,
      selectedIds.has(region.region_id),
      committedIds.has(region.region_id),
    );
  }
  if (focusedRegion && props.overview) drawFocus(context, focusedRegion);

  if (drag?.kind === "selection") {
    const left = Math.min(drag.startX, drag.currentX);
    const top = Math.min(drag.startY, drag.currentY);
    const width = Math.abs(drag.currentX - drag.startX);
    const height = Math.abs(drag.currentY - drag.startY);
    context.fillStyle = "rgb(20 142 132 / 12%)";
    context.strokeStyle = "#148e84";
    context.lineWidth = 1;
    context.setLineDash([5, 4]);
    context.fillRect(left, top, width, height);
    context.strokeRect(left + 0.5, top + 0.5, width, height);
    context.setLineDash([]);
  }
};

const drawFocus = (context: CanvasRenderingContext2D, bounds: Bounds3D) => {
  const points = [worldToScreen(bounds.min_x, bounds.min_y), worldToScreen(bounds.max_x, bounds.min_y),
    worldToScreen(bounds.min_x, bounds.max_y), worldToScreen(bounds.max_x, bounds.max_y)];
  const xs = points.map(point => point.x), ys = points.map(point => point.y);
  const left = Math.min(...xs), top = Math.min(...ys);
  const right = Math.max(...xs), bottom = Math.max(...ys);
  const centerX = (left + right) / 2, centerY = (top + bottom) / 2;
  const dx = Math.max((right - left) / 2 + 5, 10), dy = Math.max((bottom - top) / 2 + 5, 10);
  context.beginPath();
  for (const x of [-1, 1]) for (const y of [-1, 1]) {
    const cx = centerX + x * dx, cy = centerY + y * dy;
    context.moveTo(cx - x * 8, cy);
    context.lineTo(cx, cy);
    context.lineTo(cx, cy - y * 8);
  }
  context.save();
  context.lineWidth = 4;
  context.strokeStyle = "rgb(16 32 28 / 75%)";
  context.stroke();
  context.lineWidth = 1.5;
  context.strokeStyle = "#ffffff";
  context.stroke();
  context.restore();
};

const drawRaster = (
  context: CanvasRenderingContext2D, source: CanvasImageSource,
  [a, b, c, d, e, f]: [number, number, number, number, number, number],
) => {
  const origin = worldToScreen(c, f);
  const column = worldToScreen(c + a, f + d);
  const row = worldToScreen(c + b, f + e);
  context.save();
  context.transform(column.x - origin.x, column.y - origin.y, row.x - origin.x, row.y - origin.y, origin.x, origin.y);
  context.drawImage(source, 0, 0);
  context.restore();
};

const drawRegion = (
  context: CanvasRenderingContext2D,
  region: RegionViewerItem,
  isSelected: boolean,
  isCommitted: boolean,
) => {
  const extent = region.boundary_mode === "extent";
  context.beginPath();
  for (const ring of extent ? extentBoundary(region.bounds) : region.footprint_boundary_xyz) {
    if (!ring.length) continue;
    const first = worldToScreen(ring[0][0], ring[0][1]);
    context.moveTo(first.x, first.y);
    for (let index = 1; index < ring.length; index += 1) {
      const point = worldToScreen(ring[index][0], ring[index][1]);
      context.lineTo(point.x, point.y);
    }
    context.closePath();
  }
  context.fillStyle = isSelected
    ? "rgb(225 82 69 / 72%)"
    : isCommitted
      ? "rgb(92 107 104 / 54%)"
      : classificationFill(region.classification);
  context.strokeStyle = isSelected
    ? "#9f3027"
    : isCommitted
      ? "#52615e"
      : classificationStroke(region.classification);
  context.lineWidth = isSelected || isCommitted ? 2 : 1;
  context.setLineDash(extent || (isCommitted && !isSelected) ? [5, 4] : []);
  if (!extent) context.fill("evenodd");
  context.stroke();
  context.setLineDash([]);
};

const pointerPosition = (event: PointerEvent | WheelEvent) => {
  const rect = canvas.value?.getBoundingClientRect();
  return {
    x: event.clientX - (rect?.left ?? 0),
    y: event.clientY - (rect?.top ?? 0),
  };
};

const onPointerDown = (event: PointerEvent) => {
  if (!view.initialized) return;
  if (![0, 1, 2].includes(event.button)) return;
  const point = pointerPosition(event);
  drag = {
    kind:
      event.button === 0
        ? "selection"
        : event.button === 1
          ? "pan"
          : "rotate",
    startX: point.x,
    startY: point.y,
    currentX: point.x,
    currentY: point.y,
    lastX: point.x,
    lastY: point.y,
    shiftKey: event.shiftKey,
    ctrlKey: event.ctrlKey,
  };
  canvas.value?.setPointerCapture(event.pointerId);
  event.preventDefault();
};

const onPointerMove = (event: PointerEvent) => {
  if (!drag) return;
  const point = pointerPosition(event);
  if (drag.kind === "pan") {
    viewAdjusted = true;
    const screenX = (point.x - drag.lastX) / view.scale;
    const screenY = -(point.y - drag.lastY) / view.scale;
    const cosine = Math.cos(view.rotation);
    const sine = Math.sin(view.rotation);
    view.centerX -= screenX * cosine + screenY * sine;
    view.centerY -= -screenX * sine + screenY * cosine;
    drag.lastX = point.x;
    drag.lastY = point.y;
    scheduleViewport();
  } else if (drag.kind === "rotate") {
    viewAdjusted = true;
    view.rotation += (point.x - drag.lastX) * 0.008;
    drag.lastX = point.x;
    drag.lastY = point.y;
    scheduleViewport();
  } else {
    drag.currentX = point.x;
    drag.currentY = point.y;
  }
  scheduleRender();
};

const onPointerUp = (event: PointerEvent) => {
  if (!drag) return;
  const completed = drag;
  drag = undefined;
  canvas.value?.releasePointerCapture(event.pointerId);
  if (completed.kind === "selection") {
    const distance = Math.hypot(
      completed.currentX - completed.startX,
      completed.currentY - completed.startY,
    );
    const click = distance < 5;
    const mode = completed.ctrlKey ? "toggle" : completed.shiftKey ? "add" : "replace";
    if (props.exactSelection) {
      const { startX, startY, currentX, currentY } = completed;
      if (click) {
        emit("selectionQuery", [screenToWorld(currentX, currentY)], mode, pickRegion(currentX, currentY)[0]);
      } else {
        emit("selectionQuery", [
          screenToWorld(startX, startY), screenToWorld(currentX, startY),
          screenToWorld(currentX, currentY), screenToWorld(startX, currentY),
        ], mode);
      }
    } else {
      const ids = click
        ? pickRegion(completed.currentX, completed.currentY)
        : regionsInSelection(completed);
      emit("selection", { ids, mode });
    }
  }
  scheduleRender();
};

const onPointerCancel = () => {
  drag = undefined;
  scheduleRender();
};

const onWheel = (event: WheelEvent) => {
  if (!view.initialized) return;
  viewAdjusted = true;
  const point = pointerPosition(event);
  const before = screenToWorld(point.x, point.y);
  view.scale = Math.min(
    1e7,
    Math.max(1e-7, view.scale * Math.exp(-event.deltaY * 0.0012)),
  );
  const after = screenToWorld(point.x, point.y);
  view.centerX += before.x - after.x;
  view.centerY += before.y - after.y;
  scheduleRender();
  scheduleViewport();
  event.preventDefault();
};

const pickRegion = (screenX: number, screenY: number): string[] => {
  const point = screenToWorld(screenX, screenY);
  const committedIds = committed();
  for (let index = props.regions.length - 1; index >= 0; index -= 1) {
    const region = props.regions[index];
    if (committedIds.has(region.region_id)) continue;
    if (!boundsContainPoint(region.bounds, point.x, point.y)) continue;
    if (pointInBoundary(region.footprint_boundary_xyz, point.x, point.y)) {
      return [region.region_id];
    }
  }
  if (props.regionOverview) {
    const id = pickRegionOverview(props.regionOverview, point.x, point.y);
    return id && !committedIds.has(id) ? [id] : [];
  }
  const detail = props.overview && [...(props.imageryTiles ?? [])].reverse().find(tile => tile.covers(point.x, point.y));
  const overviewId = detail ? detail.pick(point.x, point.y) : props.overview?.pick(point.x, point.y);
  return overviewId ? [overviewId] : [];
};

const regionsInSelection = (selection: NonNullable<typeof drag>) => {
  const committedIds = committed();
  const minX = Math.min(selection.startX, selection.currentX);
  const minY = Math.min(selection.startY, selection.currentY);
  const maxX = Math.max(selection.startX, selection.currentX);
  const maxY = Math.max(selection.startY, selection.currentY);
  if (props.overview) {
    const corners = [
      screenToWorld(minX, minY), screenToWorld(maxX, minY),
      screenToWorld(maxX, maxY), screenToWorld(minX, maxY),
    ];
    const tiles = [...(props.imageryTiles ?? [])].reverse();
    const hits = tiles.flatMap((tile, index) => {
      const finer = tiles.slice(0, index);
      return tile.pickArea(corners, (x, y) => finer.some(other => other.covers(x, y)));
    });
    return [...new Set([...hits, ...props.overview.pickArea(corners, (x, y) => tiles.some(tile => tile.covers(x, y)))])];
  }
  return props.regions
    .filter((region) => !committedIds.has(region.region_id))
    .filter((region) =>
      boundaryIntersectsRectangle(
        region.footprint_boundary_xyz.map((ring) =>
          ring.map(([x, y]) => {
            const point = worldToScreen(x, y);
            return [point.x, point.y, 0] as [number, number, number];
          }),
        ),
        minX,
        minY,
        maxX,
        maxY,
      ),
    )
    .map((region) => region.region_id);
};

const scheduleViewport = () => {
  if (props.imageryReview) emit("viewChanging");
  if (viewportTimer) clearTimeout(viewportTimer);
  viewportTimer = setTimeout(() => {
    if (!view.initialized) return;
    const corners = [
      screenToWorld(0, 0),
      screenToWorld(view.width, 0),
      screenToWorld(view.width, view.height),
      screenToWorld(0, view.height),
    ];
    const xValues = corners.map((point) => point.x);
    const yValues = corners.map((point) => point.y);
    emit("viewport", {
      min_x: Math.min(...xValues),
      min_y: Math.min(...yValues),
      min_z: props.bounds?.min_z ?? 0,
      max_x: Math.max(...xValues),
      max_y: Math.max(...yValues),
      max_z: props.bounds?.max_z ?? 0,
    }, view.scale * ((canvas.value?.width ?? view.width) / view.width));
  }, 120);
};

const worldToScreen = (x: number, y: number) => {
  const deltaX = x - view.centerX;
  const deltaY = y - view.centerY;
  const cosine = Math.cos(view.rotation);
  const sine = Math.sin(view.rotation);
  return {
    x: (deltaX * cosine - deltaY * sine) * view.scale + view.width / 2,
    y: view.height / 2 - (deltaX * sine + deltaY * cosine) * view.scale,
  };
};

const screenToWorld = (x: number, y: number) => {
  const rotatedX = (x - view.width / 2) / view.scale;
  const rotatedY = (view.height / 2 - y) / view.scale;
  const cosine = Math.cos(view.rotation);
  const sine = Math.sin(view.rotation);
  return {
    x: rotatedX * cosine + rotatedY * sine + view.centerX,
    y: -rotatedX * sine + rotatedY * cosine + view.centerY,
  };
};

onMounted(() => {
  resizeObserver = new ResizeObserver(resize);
  if (canvas.value) resizeObserver.observe(canvas.value);
  resize();
});

onBeforeUnmount(() => {
  if (backdropImage) backdropImage.onload = null;
  resizeObserver?.disconnect();
  if (renderFrame !== undefined) cancelAnimationFrame(renderFrame);
  if (viewportTimer) clearTimeout(viewportTimer);
});

watch(() => props.backdrop?.url, (url) => {
  if (backdropImage) backdropImage.onload = null;
  backdropImage = undefined;
  if (url) {
    backdropImage = new Image();
    backdropImage.onload = scheduleRender;
    backdropImage.src = url;
  }
  scheduleRender();
}, { immediate: true });
watch(() => props.bounds, fit);
watch(() => props.regions, scheduleRender);
const overviewCanvas = computed(() => props.regionOverview
  ? createRegionOverviewCanvas(props.regionOverview, selected(), committed(), "regions")
  : null);
watch(overviewCanvas, scheduleRender);
watch(() => props.overview, scheduleRender);
watch(() => props.imageryTiles, scheduleRender);
watch(() => props.selectedRegionIds, scheduleRender);
watch(() => props.committedRegionIds, scheduleRender);

const classificationFill = (value: string | null) =>
  value?.includes("street") ? "rgb(225 185 99 / 68%)" : "rgb(91 170 148 / 68%)";

const classificationStroke = (value: string | null) =>
  value?.includes("street") ? "#9b762c" : "#2f7569";

</script>

<template>
  <div class="region-canvas-shell">
    <canvas
      ref="canvas"
      class="region-canvas"
      @contextmenu.prevent
      @pointerdown="onPointerDown"
      @pointermove="onPointerMove"
      @pointerup="onPointerUp"
      @pointercancel="onPointerCancel"
      @wheel="onWheel"
    />
    <div v-if="loading" class="region-canvas-loading" aria-live="polite">
      正在读取当前画幅
    </div>
  </div>
</template>
