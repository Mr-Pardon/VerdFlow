<script setup lang="ts">
import { onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";

import type { Bounds3D, RegionViewerItem } from "../../api";
import {
  boundaryIntersectsRectangle,
  boundsContainPoint,
  pointInBoundary,
} from "./regionGeometry";
import type { RegionSelectionMode } from "./useRegionViewer";

const props = defineProps<{
  bounds: Bounds3D | null;
  regions: RegionViewerItem[];
  selectedRegionIds: string[];
  committedRegionIds: string[];
  loading: boolean;
}>();

const emit = defineEmits<{
  viewport: [bounds: Bounds3D];
  selection: [payload: { ids: string[]; mode: RegionSelectionMode }];
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

const fit = () => {
  if (!props.bounds || view.width <= 1 || view.height <= 1) return;
  const width = Math.max(props.bounds.max_x - props.bounds.min_x, 1e-6);
  const height = Math.max(props.bounds.max_y - props.bounds.min_y, 1e-6);
  const cosine = Math.abs(Math.cos(view.rotation));
  const sine = Math.abs(Math.sin(view.rotation));
  const projectedWidth = width * cosine + height * sine;
  const projectedHeight = width * sine + height * cosine;
  view.centerX = (props.bounds.min_x + props.bounds.max_x) / 2;
  view.centerY = (props.bounds.min_y + props.bounds.max_y) / 2;
  view.scale = Math.max(
    1e-6,
    Math.min(
      (view.width - 56) / projectedWidth,
      (view.height - 56) / projectedHeight,
    ),
  );
  view.initialized = true;
  scheduleRender();
  scheduleViewport();
};

const resetRotation = () => {
  view.rotation = 0;
  fit();
};

defineExpose({ fit, resetRotation });

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
  if (changed && props.bounds && !view.initialized) fit();
  else scheduleRender();
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

  const selectedIds = selected();
  const committedIds = committed();
  for (const region of props.regions) {
    drawRegion(
      context,
      region,
      selectedIds.has(region.region_id),
      committedIds.has(region.region_id),
    );
  }

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

const drawRegion = (
  context: CanvasRenderingContext2D,
  region: RegionViewerItem,
  isSelected: boolean,
  isCommitted: boolean,
) => {
  context.beginPath();
  for (const ring of region.footprint_boundary_xyz) {
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
  context.setLineDash(isCommitted && !isSelected ? [5, 4] : []);
  context.fill("evenodd");
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
    const ids =
      distance < 5
        ? pickRegion(completed.currentX, completed.currentY)
        : regionsInSelection(completed);
    emit("selection", {
      ids,
      mode: completed.ctrlKey
        ? "toggle"
        : completed.shiftKey
          ? "add"
          : "replace",
    });
  }
  scheduleRender();
};

const onPointerCancel = () => {
  drag = undefined;
  scheduleRender();
};

const onWheel = (event: WheelEvent) => {
  if (!view.initialized) return;
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
  return [];
};

const regionsInSelection = (selection: NonNullable<typeof drag>) => {
  const committedIds = committed();
  const minX = Math.min(selection.startX, selection.currentX);
  const minY = Math.min(selection.startY, selection.currentY);
  const maxX = Math.max(selection.startX, selection.currentX);
  const maxY = Math.max(selection.startY, selection.currentY);
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
    });
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
  resizeObserver?.disconnect();
  if (renderFrame !== undefined) cancelAnimationFrame(renderFrame);
  if (viewportTimer) clearTimeout(viewportTimer);
});

watch(() => props.bounds, fit);
watch(() => props.regions, scheduleRender);
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
