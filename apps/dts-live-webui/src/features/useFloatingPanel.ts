import { ref, computed, nextTick, onMounted, onBeforeUnmount } from "vue";

export function useFloatingPanel() {

  const panelCollapsed = ref(false);

  const panelElement = ref<HTMLElement | null>(null);

  const panelPosition = ref<{ left: number; top: number } | null>(null);

  const panelDragging = ref(false);

  let panelDrag: {
    pointerId: number;
    offsetX: number;
    offsetY: number;
    handle: HTMLElement;
    width: number;
    height: number;
  } | null = null;
  let dragFrame: number | undefined;
  let pendingPosition: { left: number; top: number } | null = null;

  const panelStyle = computed(() => panelPosition.value
    ? {
        left: "0px",
        top: "0px",
        right: "auto",
        transform: `translate3d(${panelPosition.value.left}px, ${panelPosition.value.top}px, 0)`,
      }
    : undefined);

  const constrainedPanelPosition = (left: number, top: number, size?: { width: number; height: number }) => {
    const panel = panelElement.value;
    if (!panel) return { left, top };
    const rect = size ?? panel.getBoundingClientRect();
    const margin = 8;
    return {
      left: Math.min(Math.max(margin, left), Math.max(margin, window.innerWidth - rect.width - margin)),
      top: Math.min(Math.max(margin, top), Math.max(margin, window.innerHeight - rect.height - margin)),
    };
  };

  const beginPanelDrag = (event: PointerEvent) => {
    if (event.button !== 0 || !panelElement.value) return;
    if ((event.target as HTMLElement).closest("button, input, select, textarea, a")) return;
    cancelPanelDrag();
    const rect = panelElement.value.getBoundingClientRect();
    const handle = event.currentTarget as HTMLElement;
    panelDrag = {
      pointerId: event.pointerId,
      offsetX: event.clientX - rect.left,
      offsetY: event.clientY - rect.top,
      handle,
      width: rect.width,
      height: rect.height,
    };
    try {
      handle.setPointerCapture(event.pointerId);
    } catch {
      // Some DTS CEF builds do not implement pointer capture reliably.
    }
    panelDragging.value = true;
    event.preventDefault();
    event.stopPropagation();
  };

  const flushPanelDrag = () => {
    if (dragFrame !== undefined) window.cancelAnimationFrame(dragFrame);
    dragFrame = undefined;
    if (pendingPosition && panelDrag) {
      panelPosition.value = constrainedPanelPosition(pendingPosition.left, pendingPosition.top, panelDrag);
    }
    pendingPosition = null;
  };

  const movePanelDrag = (event: PointerEvent) => {
    if (!panelDrag || panelDrag.pointerId !== event.pointerId) return;
    // Keep only the newest position; input frequency may exceed CEF's paint rate.
    pendingPosition = { left: event.clientX - panelDrag.offsetX, top: event.clientY - panelDrag.offsetY };
    if (dragFrame === undefined) dragFrame = window.requestAnimationFrame(flushPanelDrag);
    event.preventDefault();
    event.stopPropagation();
  };

  const finishPanelDrag = (event: PointerEvent) => {
    if (!panelDrag || panelDrag.pointerId !== event.pointerId) return;
    if (event.type === "pointerup") {
      pendingPosition = { left: event.clientX - panelDrag.offsetX, top: event.clientY - panelDrag.offsetY };
    }
    flushPanelDrag();
    try {
      if (panelDrag.handle.hasPointerCapture(event.pointerId)) {
        panelDrag.handle.releasePointerCapture(event.pointerId);
      }
    } catch {
      // Global capture listeners still complete the drag when CEF capture fails.
    }
    panelDrag = null;
    panelDragging.value = false;
    event.preventDefault();
    event.stopPropagation();
  };

  const cancelPanelDrag = () => {
    flushPanelDrag();
    if (!panelDrag) return;
    try {
      if (panelDrag.handle.hasPointerCapture(panelDrag.pointerId)) {
        panelDrag.handle.releasePointerCapture(panelDrag.pointerId);
      }
    } catch {
      // The pointer may already have left the embedded browser surface.
    }
    panelDrag = null;
    panelDragging.value = false;
  };

  const keepPanelVisible = () => {
    if (panelDrag && panelElement.value) {
      const rect = panelElement.value.getBoundingClientRect();
      panelDrag.width = rect.width;
      panelDrag.height = rect.height;
    }
    if (!panelPosition.value) return;
    panelPosition.value = constrainedPanelPosition(
      panelPosition.value.left,
      panelPosition.value.top,
    );
  };

  const togglePanel = async () => {
    panelCollapsed.value = !panelCollapsed.value;
    await nextTick();
    keepPanelVisible();
  };

  onMounted(() => {
    window.addEventListener("resize", keepPanelVisible);
    window.addEventListener("pointermove", movePanelDrag, true);
    window.addEventListener("pointerup", finishPanelDrag, true);
    window.addEventListener("pointercancel", finishPanelDrag, true);
    window.addEventListener("blur", cancelPanelDrag);
  });
  onBeforeUnmount(() => {
    window.removeEventListener("resize", keepPanelVisible);
    window.removeEventListener("pointermove", movePanelDrag, true);
    window.removeEventListener("pointerup", finishPanelDrag, true);
    window.removeEventListener("pointercancel", finishPanelDrag, true);
    window.removeEventListener("blur", cancelPanelDrag);
    cancelPanelDrag();
  });
  return {
    panelCollapsed,
    panelElement,
    panelPosition,
    panelDragging,
    panelStyle,
    constrainedPanelPosition,
    beginPanelDrag,
    movePanelDrag,
    finishPanelDrag,
    cancelPanelDrag,
    keepPanelVisible,
    togglePanel,
  };
}
