import { onBeforeUnmount, onMounted, ref, watch, type Ref } from "vue";
import type { DtsSdkBridge } from "../sdk/DtsSdkBridge";
import { readableError } from "../shared/presentation";

type BrushMode = "include" | "exclude";
type SwitchKind = "capture" | "direction";
const SWITCH_DWELL_MS = 120;
const BRUSH_SAMPLE_MS = 80;
const WHEEL_NOTICE_MS = 950;

type ScreenPoint = { x: number; y: number };
const screenPoint = (event: MouseEvent): ScreenPoint => ({ x: event.clientX, y: event.clientY });
const samePoint = (a: ScreenPoint, b?: ScreenPoint) => a.x === b?.x && a.y === b.y;

type Stroke = {
  mode: BrushMode;
  pointerId: number;
  target: HTMLElement;
  latest: ScreenPoint;
  requested?: ScreenPoint;
  initialHits?: string[];
  seen: Set<string>;
  sampled: boolean;
  moved: boolean;
  lastQuery: number;
  querying: boolean;
};

export function useRegionBrush(
  sdk: DtsSdkBridge,
  blocked: Ref<boolean>,
  error: Ref<string | null>,
  apply: (id: string, enabled: boolean) => void,
) {
  const mode = ref<BrushMode | null>(null);
  const capturing = ref(false);
  const dragging = ref(false);
  const cursor = ref<HTMLElement | null>(null);
  const wheelMode = ref<BrushMode | null>(null);
  let stroke: Stroke | null = null;
  let released: Stroke | null = null;
  let switchTimer: number | undefined;
  let wheelNoticeTimer: number | undefined;
  let overSwitch: SwitchKind | undefined;

  const applySample = (current: Stroke, ids: string[]) => {
    if ((stroke !== current && released !== current) || !mode.value) return;
    current.sampled = true;
    if (!current.moved) { current.initialHits = ids; return; }
    for (const id of ids) {
      if (current.seen.has(id)) continue;
      current.seen.add(id);
      apply(id, current.mode === "include");
    }
  };

  const clearReleased = () => {
    if (!released) return;
    released = null;
    sdk.setRegionBrushPressed(false);
  };

  const settleReleased = (current: Stroke) => {
    if (released !== current || current.querying) return;
    if (!samePoint(current.latest, current.requested)) {
      queryScreen(current);
      return;
    }
    clearReleased();
    if (!current.sampled) error.value = "DTS 未返回场景鼠标坐标，本次没有修改选区。";
  };

  const queryScreen = (current: Stroke) => {
    current.querying = true;
    current.lastQuery = Date.now();
    const point = current.requested = current.latest;
    void sdk.sampleRegionBrushScreen(point.x, point.y, current.seen).then(ids => {
      if (ids) applySample(current, ids);
    }).catch(() => undefined).finally(() => {
      current.querying = false;
      settleReleased(current);
    });
  };

  const record = (current: Stroke, event: MouseEvent) => {
    const point = screenPoint(event);
    if (samePoint(point, current.latest)) return;
    current.latest = point;
    current.moved = true;
    if (current.initialHits) {
      applySample(current, current.initialHits);
      current.initialHits = undefined;
    }
  };

  const hideCursor = () => { if (cursor.value) cursor.value.hidden = true; };
  const showCursor = (event: MouseEvent) => {
    const marker = cursor.value;
    if (!marker) return;
    marker.style.transform = `translate3d(${event.clientX}px, ${event.clientY}px, 0) translate(-50%, -50%)`;
    marker.hidden = false;
  };
  const hoverScene = (event: PointerEvent) => {
    if (event.pointerType === "mouse" && !event.buttons) showCursor(event);
  };
  const onScene = (current: Stroke, event: MouseEvent) =>
    document.elementFromPoint(event.clientX, event.clientY) === current.target;

  const finish = (checkSamples = false) => {
    const current = stroke;
    if (!current) return;
    const { pointerId, target } = current;
    stroke = null;
    dragging.value = false;
    try {
      if (target.hasPointerCapture(pointerId)) target.releasePointerCapture(pointerId);
    } catch { /* Explorer may already have released the pointer. */ }
    if (checkSamples && current.moved) {
      // Release input now, but let this stroke's last recorded point resolve.
      released = current;
      settleReleased(current);
    } else sdk.setRegionBrushPressed(false);
  };

  const cancelSwitch = () => {
    if (switchTimer !== undefined) window.clearTimeout(switchTimer);
    switchTimer = undefined;
  };

  const leaveSwitch = (kind?: SwitchKind) => {
    if (kind && overSwitch !== kind) return;
    overSwitch = undefined;
    cancelSwitch();
  };

  const clearWheelNotice = () => {
    if (wheelNoticeTimer !== undefined) window.clearTimeout(wheelNoticeTimer);
    wheelNoticeTimer = undefined;
    wheelMode.value = null;
  };

  const stop = () => {
    leaveSwitch();
    clearWheelNotice();
    if (!mode.value) return;
    mode.value = null;
    capturing.value = false;
    hideCursor();
    finish();
    clearReleased();
    sdk.stopRegionBrush();
  };

  const toggle = (next: BrushMode) => {
    if (blocked.value || stroke) return;
    if (mode.value === next) { stop(); return; }
    clearWheelNotice();
    clearReleased();
    error.value = null;
    const wasOpen = Boolean(mode.value);
    mode.value = next;
    if (wasOpen) return;
    capturing.value = true;
    try {
      sdk.startRegionBrush(stop);
    } catch (failure) {
      stop();
      error.value = readableError(failure);
    }
  };

  const switchBrush = (kind: SwitchKind) => {
    if (!mode.value || blocked.value || stroke) return;
    if (kind === "direction") {
      if (!capturing.value) return;
      clearWheelNotice();
      mode.value = mode.value === "include" ? "exclude" : "include";
      return;
    }
    clearReleased();
    clearWheelNotice();
    hideCursor();
    capturing.value = !capturing.value;
    sdk.setRegionBrushCapture(capturing.value);
  };

  const enterSwitch = (event: PointerEvent, kind: SwitchKind) => {
    if (kind === "direction" && !capturing.value) return;
    if (overSwitch === kind || !mode.value || blocked.value || stroke || event.pointerType !== "mouse" || event.buttons !== 0) return;
    cancelSwitch();
    overSwitch = kind;
    switchTimer = window.setTimeout(() => {
      switchTimer = undefined;
      switchBrush(kind);
    }, SWITCH_DWELL_MS);
  };

  const keyboardSwitch = (event: MouseEvent, kind: SwitchKind) => {
    if (event.detail !== 0) return;
    cancelSwitch();
    switchBrush(kind);
  };

  const consume = (event: Event) => {
    event.preventDefault();
    event.stopPropagation();
  };

  const switchOnWheel = (event: WheelEvent) => {
    if (!capturing.value || !mode.value || blocked.value) return;
    consume(event);
    if (stroke || event.ctrlKey || Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return;
    const next = event.deltaY < 0 ? "include" : "exclude";
    if (mode.value === next) return;
    leaveSwitch();
    clearWheelNotice();
    mode.value = next;
    wheelMode.value = next;
    wheelNoticeTimer = window.setTimeout(clearWheelNotice, WHEEL_NOTICE_MS);
  };

  const roamOnMouseDown = (event: MouseEvent) => {
    if (!capturing.value || !mode.value || blocked.value || (event.button !== 1 && event.button !== 2)) return false;
    consume(event);
    leaveSwitch();
    finish();
    switchBrush("capture");
    return true;
  };

  const begin = (event: PointerEvent) => {
    if (event.pointerType !== "mouse" || roamOnMouseDown(event)) return;
    if (!capturing.value || !mode.value || blocked.value || stroke || event.button !== 0) return;
    consume(event);
    clearReleased();
    cancelSwitch();
    error.value = null;
    showCursor(event);
    const target = event.currentTarget as HTMLElement;
    const current = stroke = { mode: mode.value, pointerId: event.pointerId, target, latest: screenPoint(event), seen: new Set(), sampled: false, moved: false,
      lastQuery: -Infinity, querying: false };
    try { target.setPointerCapture(event.pointerId); }
    catch { /* Window listeners can still finish the stroke in CEF. */ }
    dragging.value = true;
    sdk.setRegionBrushPressed(true);
    queryScreen(current);
  };

  const otherPointer = (current: Stroke, event: MouseEvent | PointerEvent) =>
    "pointerId" in event && (event.pointerType !== "mouse" || current.pointerId !== event.pointerId);

  const move = (event: MouseEvent | PointerEvent) => {
    const current = stroke;
    if (!current || otherPointer(current, event)) return;
    if (!(event.buttons & 1)) { finish(true); return; }
    consume(event);
    if (!onScene(current, event)) { hideCursor(); return; }
    showCursor(event);
    record(current, event);
    const now = Date.now();
    if (current.querying || samePoint(current.latest, current.requested) || now - current.lastQuery < BRUSH_SAMPLE_MS) return;
    queryScreen(current);
  };
  const up = (event: MouseEvent | PointerEvent) => {
    if (!stroke || otherPointer(stroke, event)) return;
    const released = event.type === "pointerup" || event.type === "mouseup";
    if (released && event.button !== 0) return;
    if (released && onScene(stroke, event)) record(stroke, event);
    consume(event);
    finish(released);
  };
  const escape = (event: KeyboardEvent) => {
    if (mode.value && event.key === "Escape") { consume(event); stop(); }
  };
  const lost = (event: PointerEvent) => {
    if (stroke?.target === event.target && stroke?.pointerId === event.pointerId) finish();
  };
  const blur = (event: FocusEvent) => {
    // Starting a stroke disables the focused panel button, not the window.
    if (event.target !== window) return;
    leaveSwitch();
    clearWheelNotice();
    hideCursor();
    finish();
    clearReleased();
  };

  const listeners = {
    pointermove: move, mousemove: move, pointerup: up, mouseup: up, pointercancel: up,
    lostpointercapture: lost, keydown: escape, blur, pagehide: stop,
  };
  onMounted(() => {
    for (const [event, handler] of Object.entries(listeners)) window.addEventListener(event, handler as EventListener, true);
  });
  onBeforeUnmount(() => {
    for (const [event, handler] of Object.entries(listeners)) window.removeEventListener(event, handler as EventListener, true);
    stop();
  });
  watch(blocked, value => { if (value) stop(); }, { flush: "sync" });

  return { mode, capturing, dragging, blocked, cursor, wheelMode, hoverScene, hideCursor,
    toggle, stop, begin, roamOnMouseDown, switchOnWheel, enterSwitch, leaveSwitch, keyboardSwitch };
}
