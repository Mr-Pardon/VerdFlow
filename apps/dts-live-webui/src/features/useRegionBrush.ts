import { nextTick, onBeforeUnmount, onMounted, ref, watch, type Ref } from "vue";
import type { DtsSdkBridge } from "../sdk/DtsSdkBridge";
import { readableError } from "../shared/presentation";
import { DEFAULT_BRUSH_RADIUS, MAX_BRUSH_RADIUS, MIN_BRUSH_RADIUS } from "../shared/brushFootprint";

type BrushMode = "include" | "exclude";
type SwitchKind = "capture" | "direction";
type BrushShortcut = "Shift" | "Control" | "Alt";
type HostWait = typeof import("../api").waitForBrushInput;
const panelElement = (target: EventTarget | null) => Boolean((target as Element | null)?.closest?.(
  '.live-shell, dialog, input, textarea, select, [role="textbox"], [contenteditable]:not([contenteditable="false"])',
));
const SWITCH_DWELL_MS = 120;
const BRUSH_SAMPLE_MS = 80;
const DIRECTION_NOTICE_MS = 950;

type ScreenPoint = { x: number; y: number };
const screenPoint = (event: MouseEvent): ScreenPoint => ({ x: event.clientX, y: event.clientY });
const samePoint = (a: ScreenPoint, b?: ScreenPoint) => a.x === b?.x && a.y === b.y;

type Stroke = {
  mode: BrushMode;
  radius: number;
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
  needsChange: (id: string, enabled: boolean) => boolean,
  waitForHost?: HostWait,
) {
  const mode = ref<BrushMode | null>(null);
  const radius = ref(DEFAULT_BRUSH_RADIUS);
  const capturing = ref(false);
  const overPanel = ref(false);
  const panelFocused = ref(false);
  const domGesture = ref(false);
  const pageHidden = ref(Boolean(document.hidden));
  const dragging = ref(false);
  const shield = ref<HTMLElement | null>(null);
  const cursor = ref<HTMLElement | null>(null);
  const directionNotice = ref<BrushMode | null>(null);
  let stroke: Stroke | null = null;
  let released: Stroke | null = null;
  let switchTimer: number | undefined;
  let directionNoticeTimer: number | undefined;
  let pendingShortcut: BrushShortcut | undefined;
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
    void sdk.sampleRegionBrushScreen(point.x, point.y, current.radius,
      id => !current.seen.has(id) && needsChange(id, current.mode === "include")).then(ids => {
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

  const clearDirectionNotice = () => {
    if (directionNoticeTimer !== undefined) window.clearTimeout(directionNoticeTimer);
    directionNoticeTimer = undefined;
    directionNotice.value = null;
  };

  const cancelShortcut = () => { pendingShortcut = undefined; };

  const stop = () => {
    leaveSwitch();
    clearDirectionNotice();
    cancelShortcut();
    if (!mode.value) return;
    mode.value = null;
    domGesture.value = false;
    capturing.value = false;
    hideCursor();
    finish();
    clearReleased();
    sdk.stopRegionBrush();
  };

  const toggle = (next: BrushMode) => {
    if (blocked.value || stroke) return;
    if (mode.value === next) { stop(); return; }
    clearDirectionNotice();
    clearReleased();
    error.value = null;
    const wasOpen = Boolean(mode.value);
    if (!wasOpen) capturing.value = true;
    mode.value = next;
    if (wasOpen) return;
    try {
      sdk.startRegionBrush(stop);
    } catch (failure) {
      stop();
      error.value = readableError(failure);
    }
  };

  const setCapture = (next: boolean) => {
    if (!mode.value || blocked.value || stroke || capturing.value === next) return;
    clearReleased();
    clearDirectionNotice();
    cancelShortcut();
    hideCursor();
    capturing.value = next;
    sdk.setRegionBrushCapture(capturing.value);
    if (capturing.value) void nextTick(() => {
      if (!capturing.value || blocked.value || !shield.value) return;
      if (!document.hasFocus()) window.focus();
      shield.value.focus({ preventScroll: true });
    });
  };

  const switchBrush = (kind: SwitchKind) => {
    if (!mode.value || blocked.value || stroke) return;
    if (kind === "capture") { setCapture(!capturing.value); return; }
    if (capturing.value) {
      clearDirectionNotice();
      mode.value = mode.value === "include" ? "exclude" : "include";
    }
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

  const chooseDirection = (next: BrushMode) => {
    if (mode.value === next) return;
    leaveSwitch();
    clearDirectionNotice();
    mode.value = next;
    directionNotice.value = next;
    directionNoticeTimer = window.setTimeout(clearDirectionNotice, DIRECTION_NOTICE_MS);
  };

  const resizeOnWheel = (event: WheelEvent) => {
    if (!capturing.value || !mode.value || blocked.value) return;
    consume(event);
    cancelShortcut();
    if (stroke || event.ctrlKey || event.altKey || event.shiftKey || event.metaKey ||
        Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return;
    const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? window.innerHeight : 1;
    const pixels = event.deltaY * unit;
    if (Number.isFinite(pixels)) {
      radius.value = Math.max(MIN_BRUSH_RADIUS, Math.min(MAX_BRUSH_RADIUS, radius.value * Math.exp(-pixels / 550)));
    }
  };

  const auxiliaryDown = (event: MouseEvent) => {
    if (!capturing.value || !mode.value || blocked.value || (event.button !== 1 && event.button !== 2)) return;
    consume(event);
    cancelShortcut();
    leaveSwitch();
    finish();
    if (event.button === 1) chooseDirection(mode.value === "include" ? "exclude" : "include");
    else switchBrush("capture");
  };

  const begin = (event: PointerEvent) => {
    if (event.pointerType !== "mouse" || event.button !== 0 || !capturing.value || !mode.value || blocked.value || stroke) return;
    consume(event);
    clearReleased();
    cancelSwitch();
    error.value = null;
    showCursor(event);
    const target = event.currentTarget as HTMLElement;
    const current = stroke = { mode: mode.value, radius: radius.value, pointerId: event.pointerId, target, latest: screenPoint(event), seen: new Set(), sampled: false, moved: false,
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
  const canUseShortcut = (event: KeyboardEvent) => Boolean(mode.value && !blocked.value && !stroke &&
    !event.defaultPrevented && !event.isComposing && !(event.target as Element | null)?.closest?.(
      'input, textarea, select, [contenteditable]:not([contenteditable="false"]), [role="textbox"]',
    ));

  const keyDown = (event: KeyboardEvent) => {
    const { key } = event;
    if (mode.value && key === "Escape") { consume(event); stop(); return; }
    const anotherKeyDown = domGesture.value && !event.repeat;
    if (mode.value) domGesture.value = true;
    if (key !== pendingShortcut) cancelShortcut();
    if (anotherKeyDown) { cancelShortcut(); return; }
    if (!canUseShortcut(event)) return;
    if (key !== "Shift" && key !== "Control" && key !== "Alt") return;
    if (key === "Alt" ? (overPanel.value || panelFocused.value || panelElement(event.target)) : !capturing.value) return;
    if (event.metaKey || (key !== "Alt" && event.altKey) || (key !== "Shift" && event.shiftKey) || (key !== "Control" && event.ctrlKey)) return;
    if (event.repeat) return;
    // Wait for release so modified clicks and combinations stay native.
    pendingShortcut = key;
  };

  const releaseShortcut = (event: KeyboardEvent) => {
    const shortcut = pendingShortcut;
    if (event.key !== shortcut || !shortcut) return;
    cancelShortcut();
    if (!canUseShortcut(event) || event.shiftKey || event.ctrlKey || event.altKey || event.metaKey) return;
    if (shortcut === "Alt") {
      if (overPanel.value || panelFocused.value || panelElement(event.target)) return;
      consume(event);
      leaveSwitch();
      setCapture(!capturing.value);
    } else if (capturing.value) {
      consume(event);
      chooseDirection(shortcut === "Shift" ? "include" : "exclude");
    }
  };
  const keyUp = (event: KeyboardEvent) => {
    releaseShortcut(event);
    if (!event.shiftKey && !event.ctrlKey && !event.altKey && !event.metaKey) domGesture.value = false;
  };
  const lost = (event: PointerEvent) => {
    if (stroke?.target === event.target && stroke?.pointerId === event.pointerId) finish();
  };
  const blur = (event: FocusEvent) => {
    // Starting a stroke disables the focused panel button, not the window.
    if (event.target !== window) return;
    domGesture.value = panelFocused.value = overPanel.value = false;
    leaveSwitch();
    clearDirectionNotice();
    cancelShortcut();
    hideCursor();
    finish();
    clearReleased();
  };

  const listeners = {
    pointerover: (e: PointerEvent) => { overPanel.value = panelElement(e.target); },
    pointerout: (e: PointerEvent) => { overPanel.value = panelElement(e.relatedTarget); },
    focusin: (e: FocusEvent) => { panelFocused.value = panelElement(e.target); },
    focusout: (e: FocusEvent) => { panelFocused.value = panelElement(e.relatedTarget); },
    pointerdown: cancelShortcut, pointermove: move, mousemove: move, pointerup: up, mouseup: up, pointercancel: up,
    lostpointercapture: lost, keydown: keyDown, keyup: keyUp, blur, pagehide: stop,
  };
  const visibility = () => {
    pageHidden.value = document.hidden;
    if (pageHidden.value) { cancelShortcut(); domGesture.value = false; }
  };
  onMounted(() => {
    document.addEventListener?.("visibilitychange", visibility);
    for (const [event, handler] of Object.entries(listeners)) window.addEventListener(event, handler as EventListener, true);
  });
  onBeforeUnmount(() => {
    document.removeEventListener?.("visibilitychange", visibility);
    for (const [event, handler] of Object.entries(listeners)) window.removeEventListener(event, handler as EventListener, true);
    stop();
  });
  watch(blocked, value => { if (value) stop(); }, { flush: "sync" });

  // Only one outstanding, abortable wait per edit context. Never replay a result after
  // a panel interaction, a local shortcut, a stroke, or a switch to another mode.
  if (waitForHost) watch(
    () => mode.value && !blocked.value && !pageHidden.value && !dragging.value && !overPanel.value && !panelFocused.value && !domGesture.value
      ? `${mode.value}:${capturing.value ? "edit" : "roam"}` : "",
    (context, _, cleanup) => {
      if (!context) return;
      const controller = new AbortController();
      let timer: number | undefined;
      cleanup(() => { controller.abort(); window.clearTimeout(timer); });
      const hostMode = capturing.value ? "edit" : "roam";
      const poll = async () => {
        if (controller.signal.aborted) return;
        let delay = 0;
        try {
          const reply = await waitForHost(hostMode, controller.signal);
          if (controller.signal.aborted) return;
          if (!reply.available) delay = 2000;
          else if (reply.action === "edit" || reply.action === "roam") {
            leaveSwitch();
            setCapture(reply.action === "edit");
          } else if (capturing.value && (reply.action === "include" || reply.action === "exclude")) chooseDirection(reply.action);
        } catch { delay = 2000; }
        if (!controller.signal.aborted) timer = window.setTimeout(poll, delay);
      };
      void poll();
    }, { flush: "sync" },
  );

  return { mode, radius, capturing, dragging, blocked, shield, cursor, directionNotice, hoverScene, hideCursor,
    toggle, stop, begin, auxiliaryDown, resizeOnWheel, enterSwitch, leaveSwitch, keyboardSwitch };
}
