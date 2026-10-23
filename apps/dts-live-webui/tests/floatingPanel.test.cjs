const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const vue = require('vue');
const { createModuleLoader } = require('./helpers/load-typescript.cjs');

function fixture() {
  const mounted = [], disposed = [], frames = new Map(), listeners = new Map();
  let frameId = 0, rectReads = 0;
  const rect = { left: 400, top: 80, width: 380, height: 600 };
  const window = {
    innerWidth: 1360, innerHeight: 1050,
    requestAnimationFrame: callback => { frames.set(++frameId, callback); return frameId; },
    cancelAnimationFrame: id => frames.delete(id),
    addEventListener: (type, callback) => listeners.set(type, callback),
    removeEventListener: (type, callback) => {
      assert.equal(listeners.get(type), callback);
      listeners.delete(type);
    },
  };
  const handle = { setPointerCapture() {}, hasPointerCapture: () => true, releasePointerCapture() {} };
  const load = createModuleLoader({ window }, {
    vue: { ...vue, onMounted: fn => mounted.push(fn), onBeforeUnmount: fn => disposed.push(fn) },
  });
  const { useFloatingPanel } = load(path.join(__dirname, '../src/features/useFloatingPanel.ts'));
  const model = useFloatingPanel();
  model.panelElement.value = { getBoundingClientRect: () => { rectReads++; return rect; } };
  const event = (type, x = 440, y = 100, extra = {}) => ({
    type, button: 0, pointerId: 1, clientX: x, clientY: y, currentTarget: handle,
    target: { closest: () => null }, preventDefault() {}, stopPropagation() {}, ...extra,
  });
  const tick = () => {
    for (const [id, callback] of [...frames]) { frames.delete(id); callback(0); }
  };
  return { model, event, tick, frames, listeners, mounted, disposed, rect, handle, window, reads: () => rectReads };
}

test('A burst of drag events uses one frame, one size read and only the newest position', () => {
  const f = fixture();
  f.model.beginPanelDrag(f.event('pointerdown'));
  for (let i = 0; i < 240; i++) f.model.movePanelDrag(f.event('pointermove', 440 + i, 100 + i));
  assert.equal(f.frames.size, 1);
  assert.equal(f.reads(), 1);
  assert.equal(f.model.panelPosition.value, null);
  f.tick();
  assert.deepEqual({ ...f.model.panelPosition.value }, { left: 639, top: 319 });
  assert.equal(f.reads(), 1);
  assert.equal(f.frames.size, 0);
  assert.equal(f.model.panelStyle.value.transform, 'translate3d(639px, 319px, 0)');
  assert.equal(f.model.panelStyle.value.left, '0px');
});

test('Release before the next frame applies the final pointer position without a delayed replay', () => {
  const f = fixture();
  f.model.beginPanelDrag(f.event('pointerdown'));
  f.model.movePanelDrag(f.event('pointermove', 500, 160));
  f.model.finishPanelDrag(f.event('pointerup', 600, 180));
  assert.deepEqual({ ...f.model.panelPosition.value }, { left: 560, top: 160 });
  assert.equal(f.frames.size, 0);
  assert.equal(f.model.panelDragging.value, false);
  f.tick();
  assert.equal(f.model.panelPosition.value.left, 560);
});

test('Global capture still handles movement outside the panel when CEF pointer capture fails', () => {
  const f = fixture();
  f.handle.setPointerCapture = () => { throw new Error('not supported'); };
  f.mounted.forEach(fn => fn());
  f.model.beginPanelDrag(f.event('pointerdown'));
  f.listeners.get('pointermove')(f.event('pointermove', -500, 3000));
  f.tick();
  assert.deepEqual({ ...f.model.panelPosition.value }, { left: 8, top: 442 });
  f.listeners.get('pointercancel')(f.event('pointercancel', 0, 0));
  assert.equal(f.model.panelPosition.value.top, 442);
  assert.equal(f.model.panelDragging.value, false);
});

test('Resize refreshes drag bounds and collapse keeps the panel inside the viewport', async () => {
  const f = fixture();
  f.mounted.forEach(fn => fn());
  f.model.beginPanelDrag(f.event('pointerdown'));
  f.model.movePanelDrag(f.event('pointermove', 1200, 500));
  f.tick();
  f.window.innerWidth = 430;
  f.rect.width = 380;
  f.listeners.get('resize')();
  f.model.movePanelDrag(f.event('pointermove', 1200, 500));
  f.tick();
  assert.equal(f.model.panelPosition.value.left, 42);
  f.model.finishPanelDrag(f.event('pointerup', 1200, 500));
  f.rect.width = 100;
  await f.model.togglePanel();
  assert.equal(f.model.panelCollapsed.value, true);
  assert.equal(f.model.panelPosition.value.left, 42);
});

test('Blur and unmount flush the last position, cancel animation frames and remove listeners', () => {
  const f = fixture();
  f.mounted.forEach(fn => fn());
  f.model.beginPanelDrag(f.event('pointerdown'));
  f.model.movePanelDrag(f.event('pointermove', 500, 150));
  f.listeners.get('blur')();
  assert.equal(f.frames.size, 0);
  assert.equal(f.model.panelDragging.value, false);
  assert.equal(f.model.panelPosition.value.left, 460);
  f.model.beginPanelDrag(f.event('pointerdown'));
  f.model.movePanelDrag(f.event('pointermove', 600, 200));
  f.disposed.forEach(fn => fn());
  assert.equal(f.frames.size, 0);
  assert.equal(f.listeners.size, 0);
  assert.equal(f.model.panelDragging.value, false);
});

test('Controls and unrelated pointer IDs do not start or interfere with dragging', () => {
  const f = fixture();
  f.model.beginPanelDrag(f.event('pointerdown', 440, 100, { target: { closest: () => ({}) } }));
  assert.equal(f.model.panelDragging.value, false);
  assert.equal(f.reads(), 0);
  f.model.beginPanelDrag(f.event('pointerdown'));
  f.model.movePanelDrag(f.event('pointermove', 500, 150, { pointerId: 2 }));
  f.model.finishPanelDrag(f.event('pointerup', 500, 150, { pointerId: 2 }));
  assert.equal(f.frames.size, 0);
  assert.equal(f.model.panelDragging.value, true);
  f.model.cancelPanelDrag();
});
