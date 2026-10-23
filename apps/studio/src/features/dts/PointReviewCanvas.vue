<script setup lang="ts">
import { onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";

import type { Bounds3D, PointReviewItem, RegionViewerItem } from "../../api";

type PointReviewCanvasItem = PointReviewItem & {
  reference_radius_m: number | null;
};

const props = defineProps<{
  bounds: Bounds3D | null;
  regions: RegionViewerItem[];
  points: PointReviewCanvasItem[];
  colors: Record<string, string>;
  loading: boolean;
  showReferenceRanges: boolean;
  pointDetailsVisible: boolean;
  plantedRegionIds: string[];
}>();

const emit = defineEmits<{
  viewport: [bounds: Bounds3D];
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
      kind: "pan" | "rotate";
      lastX: number;
      lastY: number;
    }
  | undefined;

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
  const width = Math.max(1, Math.round(rect.width));
  const height = Math.max(1, Math.round(rect.height));
  const changed = width !== view.width || height !== view.height;
  view.width = width;
  view.height = height;
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  element.width = Math.round(width * ratio);
  element.height = Math.round(height * ratio);
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
  const context = element?.getContext("2d");
  if (!element || !context) return;
  const ratio = element.width / view.width;
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, view.width, view.height);
  context.fillStyle = "#edf3f1";
  context.fillRect(0, 0, view.width, view.height);

  const plantedRegionIds = new Set(props.plantedRegionIds);
  for (const region of props.regions) {
    const planted = plantedRegionIds.has(region.region_id);
    drawRegion(context, region, planted && !props.pointDetailsVisible);
  }

  if (!props.pointDetailsVisible) return;

  const centerRadius = Math.max(1.8, Math.min(4.2, view.scale * 0.45));
  if (props.showReferenceRanges) {
    for (const point of props.points) {
      if (point.reference_radius_m === null) continue;
      drawReferenceRange(context, point, centerRadius);
    }
  }
  for (const point of props.points) {
    const screen = worldToScreen(point.position[0], point.position[1]);
    if (
      screen.x < -centerRadius ||
      screen.y < -centerRadius ||
      screen.x > view.width + centerRadius ||
      screen.y > view.height + centerRadius
    ) {
      continue;
    }
    context.beginPath();
    context.arc(screen.x, screen.y, centerRadius, 0, Math.PI * 2);
    context.fillStyle = props.colors[point.asset_id] ?? "#d95d4f";
    context.fill();
    context.strokeStyle = "rgb(255 255 255 / 78%)";
    context.lineWidth = 0.8;
    context.stroke();
  }
};

const drawReferenceRange = (
  context: CanvasRenderingContext2D,
  point: PointReviewCanvasItem,
  minimumRadius: number,
) => {
  const worldRadius = (point.reference_radius_m ?? 0) * Math.max(
    Math.abs(point.target_scale_xyz[0]),
    Math.abs(point.target_scale_xyz[1]),
  );
  const radius = Math.max(minimumRadius, worldRadius * view.scale);
  const screen = worldToScreen(point.position[0], point.position[1]);
  if (
    screen.x < -radius ||
    screen.y < -radius ||
    screen.x > view.width + radius ||
    screen.y > view.height + radius
  ) {
    return;
  }
  const color = props.colors[point.asset_id] ?? "#d95d4f";
  context.save();
  context.beginPath();
  context.arc(screen.x, screen.y, radius, 0, Math.PI * 2);
  context.globalAlpha = 0.16;
  context.fillStyle = color;
  context.fill();
  context.globalAlpha = 0.62;
  context.strokeStyle = color;
  context.lineWidth = 1;
  context.stroke();
  context.restore();
};

const drawRegion = (
  context: CanvasRenderingContext2D,
  region: RegionViewerItem,
  plantedOverview = false,
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
  context.fillStyle = plantedOverview
    ? "rgb(46 143 122 / 62%)"
    : classificationFill(region.classification);
  context.strokeStyle = plantedOverview
    ? "#1c6f62"
    : classificationStroke(region.classification);
  context.lineWidth = plantedOverview ? 1.4 : 1;
  context.fill("evenodd");
  context.stroke();
};

const pointerPosition = (event: PointerEvent | WheelEvent) => {
  const rect = canvas.value?.getBoundingClientRect();
  return {
    x: event.clientX - (rect?.left ?? 0),
    y: event.clientY - (rect?.top ?? 0),
  };
};

const onPointerDown = (event: PointerEvent) => {
  if (!view.initialized || ![1, 2].includes(event.button)) return;
  const point = pointerPosition(event);
  drag = {
    kind: event.button === 1 ? "pan" : "rotate",
    lastX: point.x,
    lastY: point.y,
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
  } else {
    view.rotation += (point.x - drag.lastX) * 0.008;
  }
  drag.lastX = point.x;
  drag.lastY = point.y;
  scheduleRender();
  scheduleViewport();
};

const onPointerUp = (event: PointerEvent) => {
  if (!drag) return;
  drag = undefined;
  canvas.value?.releasePointerCapture(event.pointerId);
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
    emit("viewport", {
      min_x: Math.min(...corners.map((point) => point.x)),
      min_y: Math.min(...corners.map((point) => point.y)),
      min_z: props.bounds?.min_z ?? 0,
      max_x: Math.max(...corners.map((point) => point.x)),
      max_y: Math.max(...corners.map((point) => point.y)),
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
watch(() => props.points, scheduleRender);
watch(() => props.colors, scheduleRender);
watch(() => props.showReferenceRanges, scheduleRender);
watch(() => props.pointDetailsVisible, scheduleRender);
watch(() => props.plantedRegionIds, scheduleRender);

const classificationFill = (value: string | null) =>
  value?.includes("street") ? "rgb(225 185 99 / 48%)" : "rgb(91 170 148 / 48%)";

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
      @pointercancel="drag = undefined"
      @wheel="onWheel"
    />
    <div v-if="loading" class="region-canvas-loading" aria-live="polite">
      正在读取当前画幅
    </div>
  </div>
</template>
